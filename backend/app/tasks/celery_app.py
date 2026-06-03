import time
import random
from celery import Celery
from app.config import settings

celery_app = Celery(
    "unipus_helper",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.celery_app"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600 * 4,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
)


def _save_progress(db, task_id: int, progress: float, log: str, status: str = None):
    from app.models import Task
    task = db.query(Task).filter(Task.id == task_id).first()
    if task:
        task.progress = progress
        task.log = log
        if status:
            task.status = status
        db.commit()


@celery_app.task(bind=True, max_retries=3)
def process_order_task(self, task_id: int):
    from sqlalchemy.orm import sessionmaker
    from app.models import Task, User, engine
    from app.services.unipus import UnipusClient, is_api_success
    from app.services.decrypt import decrypt_aes, extract_answers, build_submit_body, is_supported
    from app.services.email import (
        notify_login_success, notify_login_failed,
        notify_order_completed, notify_unavailable, notify_admin
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return {"error": "任务不存在"}

        task.status = "running"
        task.celery_task_id = self.request.id
        task.log = "Celery 任务开始执行..."
        db.commit()

        from app.services.crypto import decrypt_password
        client = UnipusClient(
            phone=task.phone,
            password=decrypt_password(task.password),
            email=task.email
        )

        # 登录
        self.update_state(state="PROGRESS", meta={"status": "登录中...", "progress": 5})
        _save_progress(db, task_id, 5.0, "正在登录 U校园AI...", "running")

        if not client.login():
            task.status = "failed"
            task.progress = 0.0
            task.log = "\n".join(client.logs)
            db.commit()
            notify_login_failed(task.email, phone=task.phone, error="账号或密码错误")
            notify_admin("U校园AI登录失败", f"账号: {task.phone}", user_info={"phone": task.phone}, logs=client.logs)
            return {"error": "登录失败", "logs": client.logs}

        # 登录成功：锁定手机号到用户
        user = db.query(User).filter(User.id == task.user_id).first()
        if user and not user.default_phone:
            user.default_phone = task.phone
            db.commit()

        # 获取用户信息
        self.update_state(state="PROGRESS", meta={"status": "获取用户信息...", "progress": 15})
        _save_progress(db, task_id, 15.0, "\n".join(client.logs[-10:]), "running")

        client.generate_auth_token()
        client.fetch_user_info()

        # 获取课程列表
        self.update_state(state="PROGRESS", meta={"status": "获取课程列表...", "progress": 25})
        _save_progress(db, task_id, 25.0, "\n".join(client.logs[-10:]), "running")

        courses = client.fetch_course_list()
        if not courses:
            task.status = "failed"
            task.progress = 0.0
            task.log = "\n".join(client.logs)
            db.commit()
            notify_unavailable(task.email, courses=[], reason="未检测到任何课程")
            notify_admin("无可执行课程", f"账号: {task.phone}", user_info=client.user_info, logs=client.logs)
            return {"error": "没有课程", "logs": client.logs}

        # 登录成功通知（传入实际课程列表）
        notify_login_success(task.email, user_info=client.user_info, courses=courses)

        # 提取所有课程资源
        all_resource_ids = []
        for course in courses:
            for resource in course.get("courseResourceList", []):
                if resource.get("id"):
                    all_resource_ids.append(resource)

        total_resources = len(all_resource_ids)
        client.log(f"共 {total_resources} 个课程资源")

        self.update_state(state="PROGRESS", meta={"status": "处理课程资源...", "progress": 40})
        _save_progress(db, task_id, 40.0, "\n".join(client.logs[-10:]), "running")

        has_done_any = False
        task_count = 0
        submitted_task_ids = set()  # 全局：整个执行期间每个任务只提交一次

        for idx, resource in enumerate(all_resource_ids):
            resource_id = resource["id"]
            strategy_id = resource.get("strategyId", "")
            base_progress = 40 + (idx / max(total_resources, 1)) * 50

            client.log(f"处理课程资源: {resource_id}")
            _save_progress(db, task_id, base_progress, "\n".join(client.logs[-10:]), "running")

            resource_detail = client.fetch_course_resource(resource_id)
            if not resource_detail:
                continue

            course_resource = resource_detail.get("courseResource", {})
            course_instance_id = course_resource.get("courseInstanceId")
            course_resource_id = course_resource.get("courseResourceId")
            course_id = course_resource.get("courseId")

            # 获取单元资源
            unit_resource = client.fetch_unit_resource(course_instance_id)
            if not unit_resource:
                continue

            # 获取任务策略
            strategy = client.fetch_course_strategy(strategy_id, resource_id)
            if not strategy:
                continue

            strategy_list = strategy.get("courseUnitStrategyList", [])
            base_map = client.extract_base_types(unit_resource, strategy_list)
            supported = {k: v for k, v in base_map.items() if is_supported(v)}

            if not supported:
                client.log("当前课程无支持题型")
                continue

            # 获取单元列表
            unit_situation = client.fetch_unit_situation(resource_id)
            if not unit_situation or not is_api_success(unit_situation):
                continue

            unit_list = unit_situation.get("value", {}).get("unitList", [])
            instant_id = strategy.get("courseStudyStrategy", {}).get("instantId")
            total_units = len(unit_list)

            for u_idx, unit in enumerate(unit_list):
                node_id = unit.get("nodeId")
                if not node_id:
                    continue

                unit_progress = base_progress + (u_idx / max(total_units, 1)) * (50 / max(total_resources, 1))
                _save_progress(db, task_id, unit_progress, "\n".join(client.logs[-10:]), "running")

                # 查询时限并提取 tutorialId
                tutorial_id = instant_id
                time_limit = client.fetch_unit_time_limit(instant_id, node_id)
                if time_limit and time_limit.get("rt"):
                    rt = time_limit["rt"]
                    if rt.get("tutorialId"):
                        tutorial_id = rt["tutorialId"]
                    leaves = rt.get("leafs", {})
                    task_info = leaves.get(node_id, {})
                    strategies = task_info.get("strategies", {})
                    st = strategies.get("start_time")
                    et = strategies.get("end_time")
                    now = int(time.time())
                    if st is not None and et is not None:
                        if not (st < now < et):
                            client.log(f"单元 {node_id} 不在时间范围内")
                            _save_progress(db, task_id, unit_progress, "\n".join(client.logs[-10:]), "running")
                            continue

                # 获取单元任务详情（含必修状态、完成状态）
                task_status_list = client.fetch_unit_task_detail(node_id, course_resource_id)
                if not task_status_list or not task_status_list[0].get("required", False):
                    client.log(f"单元 {node_id} 非必修，跳过")
                    _save_progress(db, task_id, unit_progress, "\n".join(client.logs[-10:]), "running")
                    continue

                supported_tasks = list(supported.items())
                total_tasks = len(supported_tasks)
                for t_idx, (task_item_id, question_type) in enumerate(supported_tasks):
                    # 任务级进度：在 unit_progress 基础上按任务比例递增
                    task_range = (50 / max(total_resources, 1)) / max(total_units, 1)
                    task_progress = unit_progress + (t_idx / max(total_tasks, 1)) * task_range

                    # 本地已提交或 API 返回已完成，均跳过
                    if task_item_id in submitted_task_ids or client.is_task_completed(task_status_list, task_item_id):
                        client.log(f"任务 {task_item_id} 已完成，跳过")
                        _save_progress(db, task_id, task_progress, "\n".join(client.logs[-10:]), "running")
                        continue

                    answer_data, answer_key = client.fetch_task_answer(tutorial_id, task_item_id)
                    if not answer_data:
                        client.log(f"任务 {task_item_id} 无答案数据")
                        _save_progress(db, task_id, task_progress, "\n".join(client.logs[-10:]), "running")
                        continue

                    decrypted = decrypt_aes(answer_data, answer_key)
                    instance_id, answers = extract_answers(decrypted)

                    try:
                        body = build_submit_body(
                            instance_id, answers, task_item_id,
                            instant_id, client.openid, question_type
                        )
                    except ValueError as e:
                        client.log(f"构造请求体失败: {e}")
                        _save_progress(db, task_id, task_progress, "\n".join(client.logs[-10:]), "running")
                        continue

                    success, resp_data = client.submit_answer(body)
                    if success:
                        client.log(f"任务 {task_item_id} 提交成功")
                        has_done_any = True
                        task_count += 1
                        submitted_task_ids.add(task_item_id)
                    else:
                        client.log(f"任务 {task_item_id} 提交失败: {resp_data}")

                    # 提交后立刻保存进度+日志
                    _save_progress(db, task_id, task_progress, "\n".join(client.logs[-10:]), "running")

                    if resp_data.get("data") and resp_data["data"].get("record_grade"):
                        ts = resp_data["data"]["record_grade"].get("ts", int(time.time()))
                        client.do_extra_request(course_id or instant_id, task_item_id, ts)

                    time.sleep(random.randint(30, 60))

        # 最终状态
        task.log = "\n".join(client.logs)
        if has_done_any:
            task.status = "completed"
            task.progress = 100.0
            notify_order_completed(task.email, progress=None, task_count=task_count)
            notify_admin("U校园AI任务完成", f"账号: {task.phone}\n完成任务: {task_count} 个", user_info=client.user_info, logs=client.logs)
        else:
            task.status = "failed"
            task.progress = 0.0
            reason = "当前课程可能不在开放时间内，或所有任务已完成。"
            notify_unavailable(task.email, courses=courses, reason=reason)
            notify_admin("无可执行任务", f"账号: {task.phone}\n{reason}", user_info=client.user_info, logs=client.logs)
        db.commit()

        return {"success": has_done_any, "logs": client.logs, "task_count": task_count}

    except Exception as exc:
        db.rollback()
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.status = "failed"
            task.progress = 0.0
            task.log = task.log + f"\n[ERROR] {str(exc)}"
            db.commit()
            _client = locals().get('client')
            notify_admin(
                "U校园AI任务异常",
                f"账号: {task.phone}\n异常: {exc}",
                user_info=getattr(_client, 'user_info', None) if _client else None,
                logs=getattr(_client, 'logs', []) if _client else []
            )
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
