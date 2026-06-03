import requests
import json
import jwt
import time
import random
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

from app.config import settings
from app.services.decrypt import (
    decrypt_aes, extract_answers, build_submit_body,
    normalize_base_type, is_supported, INVALID_TYPES, PRESET_MODES, STUDY_MODES
)
from app.services.email import (
    notify_login_success, notify_login_failed,
    notify_order_completed, notify_unavailable, notify_admin
)

def is_api_success(data: dict) -> bool:
    """U校园 API 成功判断：兼容 code==0 和 success==True 两种返回格式"""
    if not data:
        return False
    return data.get("code") == 0 or data.get("success") is True

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json"
}

class UnipusClient:
    def __init__(self, phone: str, password: str, email: str):
        self.phone = phone
        self.password = password
        self.email = email
        self.session = requests.Session()
        self.session.headers.update(HEADERS_BASE)

        self.jwt_token: Optional[str] = None
        self.openid: Optional[str] = None
        self.auth_token: Optional[str] = None
        self.app_user_id: Optional[str] = None
        self.sso_id: Optional[str] = None
        self.user_info: Optional[Dict] = None

        self.logs: List[str] = []
        self.initial_progress: Optional[Dict] = None
        self.final_progress: Optional[Dict] = None

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.logs.append(line)
        print(line)

    # ========== 1. 登录 ==========
    def login(self) -> bool:
        url = "https://sso.unipus.cn/sso/0.1/sso/login"
        payload = {
            "username": self.phone,
            "password": self.password,
            "remember": True,
            "agreement": True,
            "service": "https://uai.unipus.cn/home"
        }
        try:
            resp = self.session.post(url, json=payload, timeout=30)
            data = resp.json()
            if is_api_success(data) or data.get("rs"):
                self.jwt_token = data["rs"]["jwt"]
                self.openid = data["rs"]["openid"]
                self.log(f"登录成功, openid={self.openid[:16]}...")
                return True
            else:
                self.log(f"登录失败: {data}")
                return False
        except Exception as e:
            self.log(f"登录异常: {e}")
            return False

    # ========== 2. 生成 authToken ==========
    # U校园AI 服务端验证密钥（固定值，不可更改）
    UNIPUS_AUTH_SECRET = "a824b379f126b8b7aa5e33dee83fb0a05aa7462c"

    def generate_auth_token(self) -> str:
        payload = {
            "aud": "edx.unipus.cn",
            "administrator": False,
            "open_id": self.openid,
            "name": "",
            "iss": "c4f772063dcfa98e9c50",
            "exp": int(time.time()) + 365 * 24 * 3600,
            "email": ""
        }
        self.auth_token = jwt.encode(payload, self.UNIPUS_AUTH_SECRET, algorithm="HS256")
        return self.auth_token

    # ========== 3. 获取用户信息 ==========
    def fetch_user_info(self) -> bool:
        url = "https://uai.unipus.cn/api/account/user/info"
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            data = resp.json()
            if is_api_success(data):
                self.user_info = data.get("value", {}).get("userInfo", {})
                self.app_user_id = self.user_info.get("appUserId")
                self.sso_id = self.user_info.get("ssoId")
                self.log(f"获取用户信息成功, appUserId={self.app_user_id}")
                return True
            else:
                self.log(f"获取用户信息失败: {data}")
                return False
        except Exception as e:
            self.log(f"获取用户信息异常: {e}")
            return False

    # ========== 4. 获取课程列表 ==========
    def fetch_course_list(self) -> List[Dict]:
        url = "https://uai.unipus.cn/api/cmgt/course/getCourseListByStudent"
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            data = resp.json()
            if is_api_success(data):
                courses = data.get("value", {}).get("courseList", [])
                self.log(f"获取课程列表成功, 共 {len(courses)} 门课程")
                return courses
            else:
                self.log(f"获取课程列表失败: {data}")
                return []
        except Exception as e:
            self.log(f"获取课程列表异常: {e}")
            return []

    # ========== 5. 获取课程资源详情 ==========
    def fetch_course_resource(self, resource_id: str) -> Optional[Dict]:
        url = f"https://uai.unipus.cn/api/cmgt/course/getCourseResourceInfoById/{resource_id}"
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            data = resp.json()
            if is_api_success(data):
                return data.get("value", {})
            return None
        except Exception as e:
            self.log(f"获取课程资源异常: {e}")
            return None

    # ========== 6. 获取单元资源 ==========
    def fetch_unit_resource(self, course_instance_id: str) -> Optional[Dict]:
        url = f"https://ucontent.unipus.cn/course/api/course/{course_instance_id}/default"
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            return resp.json()
        except Exception as e:
            self.log(f"获取单元资源异常: {e}")
            return None

    # ========== 7. 获取任务ID（学习策略） ==========
    def fetch_course_strategy(self, strategy_id: str, course_resource_id: str) -> Optional[Dict]:
        url = "https://uai.unipus.cn/api/tla/courseStudyStrategy/detail"
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        payload = {
            "id": strategy_id,
            "courseResourceId": course_resource_id
        }
        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=30)
            data = resp.json()
            if is_api_success(data):
                return data.get("value", {})
            return None
        except Exception as e:
            self.log(f"获取任务策略异常: {e}")
            return None

    # ========== 8. 提取 baseType ==========
    @staticmethod
    def extract_base_types(course_data: Dict, strategy_list: List[Dict]) -> Dict[str, str]:
        base_map = {}

        def walk(nodes):
            for n in nodes or []:
                if n.get("id"):
                    base_map[n["id"]] = n.get("base", "UNKNOWN")
                if n.get("children"):
                    walk(n["children"])

        course_str = course_data.get("course", "")
        if isinstance(course_str, str):
            try:
                course = json.loads(course_str)
                walk(course.get("units", []))
            except json.JSONDecodeError:
                pass

        out = {}
        for st in strategy_list or []:
            for task_id in st.get("requiredTask", []):
                if task_id in base_map:
                    out[task_id] = base_map[task_id]
        return out

    # ========== 9. 获取单元ID列表 ==========
    def fetch_unit_situation(self, resource_id: str) -> Optional[Dict]:
        url = (f"https://uai.unipus.cn/api/tla/learningDetail/studyRecord/"
               f"totalAndUnitSituation?id={resource_id}&appUserId={self.app_user_id}")
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            return resp.json()
        except Exception as e:
            self.log(f"获取单元情况异常: {e}")
            return None

    # ========== 10. 单元时限查询 ==========
    def fetch_unit_time_limit(self, instant_id: str, node_id: str) -> Optional[Dict]:
        url = (f"https://ucontent.unipus.cn/course/api/v2/course_progress/"
               f"{instant_id}/{node_id}/{self.openid}/default")
        headers = {
            **HEADERS_BASE,
            "Authorization": self.jwt_token,
            "x-annotator-auth-token": self.auth_token
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            return resp.json()
        except Exception as e:
            self.log(f"时限查询异常: {e}")
            return None

    # ========== 11. 获取单元任务详情（含必修状态、完成状态） ==========
    def fetch_unit_task_detail(self, node_id: str, course_resource_id: str) -> Optional[List[Dict]]:
        url = (f"https://uai.unipus.cn/api/tla/learningDetail/studyRecord/unitTaskSituation?"
               f"nodeId={node_id}&id={course_resource_id}&appUserId={self.app_user_id}&ssoId={self.sso_id}")
        headers = {**HEADERS_BASE, "Authorization": self.jwt_token}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            data = resp.json()
            if is_api_success(data):
                value = data.get("value", {})
                task_list = value.get("list", [])
                # 打印完整返回用于调试确认数据结构
                if task_list:
                    self.log(f"unitTaskSituation 返回 {len(task_list)} 条任务状态: {task_list[:2]}")
                return task_list
            return None
        except Exception as e:
            self.log(f"获取单元任务详情异常: {e}")
            return None

    def is_task_completed(self, task_status_list: List[Dict], task_id: str) -> bool:
        """递归检查 unitTaskSituation 返回的树形结构中，指定 task_id 是否已完成。"""
        if not task_status_list:
            return False

        def _walk(nodes):
            for item in nodes:
                item_id = item.get("nodeId") or item.get("id") or item.get("taskId") or item.get("base") or item.get("baseType") or item.get("task_id")
                if item_id and str(item_id) == str(task_id):
                    # 尝试多种完成判断条件
                    score = item.get("score")
                    if score is not None and float(score) >= 100:
                        return True
                    finish_progress = item.get("finishProgress")
                    if finish_progress is not None and float(finish_progress) >= 100:
                        return True
                    if item.get("complete") is True or item.get("completed") is True:
                        return True
                    if item.get("finished") is True:
                        return True
                    if item.get("status") in ("completed", "done", "finished"):
                        return True
                    progress = item.get("progress")
                    if progress is not None and float(progress) >= 100:
                        return True
                    return False
                # 递归检查 children
                children = item.get("children")
                if children:
                    result = _walk(children)
                    if result is not None:
                        return result
            return None

        result = _walk(task_status_list)
        return result if result is not None else False

    # ========== 12. 获取任务答案 ==========
    def fetch_task_answer(self, tutorial_id: str, task_id: str) -> Tuple[Optional[str], Optional[str]]:
        url = f"https://ucontent.unipus.cn/course/api/v3/answer/{tutorial_id}/{task_id}/default"
        headers = {
            **HEADERS_BASE,
            "Authorization": self.jwt_token,
            "x-annotator-auth-token": self.auth_token
        }
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            data = resp.json()
            if is_api_success(data):
                # 兼容两种返回格式：顶层直接返回 或 嵌套在 rt 中
                answer_data = data.get("data") or data.get("rt", {}).get("data")
                answer_key = data.get("k") or data.get("rt", {}).get("k")
                return answer_data, answer_key
            return None, None
        except Exception as e:
            self.log(f"获取答案异常: {e}")
            return None, None

    # ========== 13. 提交答案 ==========
    def submit_answer(self, body: str) -> Tuple[bool, Dict]:
        url = "https://ucontent.unipus.cn/course/api/v3/newExploration/submit"
        headers = {
            **HEADERS_BASE,
            "x-annotator-auth-token": self.auth_token
        }
        try:
            resp = self.session.post(url, headers=headers, data=body, timeout=30)
            data = resp.json()
            success = is_api_success(data)
            return success, data
        except Exception as e:
            self.log(f"提交答案异常: {e}")
            return False, {"error": str(e)}

    # ========== 14. 额外请求 ==========
    def do_extra_request(self, course_id: str, task_id: str, ts: int):
        url = (f"https://ucontent.unipus.cn/api/mobile/user_module/"
               f"{course_id}/{task_id}-{ts}")
        headers = {
            **HEADERS_BASE,
            "Authorization": self.jwt_token,
            "x-annotator-auth-token": self.auth_token
        }
        try:
            self.session.get(url, headers=headers, timeout=10)
        except Exception:
            pass

    # ========== 15. 获取最终进度 ==========
    def fetch_final_progress(self, resource_id: str) -> Optional[Dict]:
        return self.fetch_unit_situation(resource_id)

    # ========== 核心流程 ==========
    def run(self) -> Dict[str, Any]:
        result = {
            "success": False,
            "logs": self.logs,
            "message": "",
            "initial_progress": None,
            "final_progress": None,
            "delta": None
        }

        # Step 1: 登录
        if not self.login():
            result["message"] = "登录失败"
            notify_login_failed(self.email, phone=self.phone, error="账号或密码错误")
            notify_admin("U校园AI登录失败", f"账号: {self.phone}", user_info={"phone": self.phone}, logs=self.logs)
            return result

        # Step 2: 生成 authToken
        self.generate_auth_token()

        # Step 3: 获取用户信息
        if not self.fetch_user_info():
            result["message"] = "获取用户信息失败"
            return result

        # Step 4: 获取课程列表
        courses = self.fetch_course_list()
        if not courses:
            result["message"] = "没有课程"
            notify_unavailable(self.email, courses=[], reason="未检测到任何课程")
            notify_admin("无可执行课程", f"账号: {self.phone}", user_info=self.user_info, logs=self.logs)
            return result

        # 登录成功通知（携带用户信息和课程列表）
        notify_login_success(self.email, user_info=self.user_info, courses=courses)

        # 提取所有课程资源ID
        all_resource_ids = []
        for course in courses:
            for resource in course.get("courseResourceList", []):
                if resource.get("id"):
                    all_resource_ids.append(resource)

        self.log(f"共 {len(all_resource_ids)} 个课程资源")

        # 记录初始进度（取第一个资源）
        if all_resource_ids:
            initial_data = self.fetch_unit_situation(all_resource_ids[0]["id"])
            if initial_data and is_api_success(initial_data):
                self.initial_progress = initial_data.get("value", {}).get("totalDetail", {})
                result["initial_progress"] = self.initial_progress
                self.log(f"初始进度: {self.initial_progress}")

        # 遍历每个课程资源
        has_done_any = False
        task_count = 0
        submitted_task_ids = set()  # 全局：整个执行期间每个任务只提交一次
        for resource in all_resource_ids:
            resource_id = resource["id"]
            strategy_id = resource.get("strategyId", "")
            self.log(f"处理课程资源: {resource_id}")

            # 获取课程资源详情
            resource_detail = self.fetch_course_resource(resource_id)
            if not resource_detail:
                continue

            course_resource = resource_detail.get("courseResource", {})
            course_instance_id = course_resource.get("courseInstanceId")
            course_resource_id = course_resource.get("courseResourceId")
            course_id = course_resource.get("courseId")

            # 获取单元资源
            unit_resource = self.fetch_unit_resource(course_instance_id)
            if not unit_resource:
                continue

            # 获取任务策略
            strategy = self.fetch_course_strategy(strategy_id, resource_id)
            if not strategy:
                continue

            strategy_list = strategy.get("courseUnitStrategyList", [])
            base_map = self.extract_base_types(unit_resource, strategy_list)
            self.log(f"提取到 {len(base_map)} 个任务基础类型")

            # 过滤支持的题型
            supported = {k: v for k, v in base_map.items() if is_supported(v)}
            unsupported = {k: v for k, v in base_map.items() if not is_supported(v)}
            self.log(f"支持: {len(supported)}, 不支持: {len(unsupported)}")

            if not supported:
                self.log("当前课程无支持题型")
                continue

            # 获取单元列表
            unit_situation = self.fetch_unit_situation(resource_id)
            if not unit_situation or not is_api_success(unit_situation):
                continue

            unit_list = unit_situation.get("value", {}).get("unitList", [])
            instant_id = strategy.get("courseStudyStrategy", {}).get("instantId")

            # 遍历单元
            for unit in unit_list:
                node_id = unit.get("nodeId")
                if not node_id:
                    continue

                self.log(f"处理单元: {node_id}")

                # 查询时限
                tutorial_id = instant_id  # 默认回退
                time_limit = self.fetch_unit_time_limit(instant_id, node_id)
                if time_limit and time_limit.get("rt"):
                    rt = time_limit["rt"]
                    # 提取 tutorialId（答案接口需要）
                    if rt.get("tutorialId"):
                        tutorial_id = rt["tutorialId"]
                    leaves = rt.get("leafs", {})
                    task = leaves.get(node_id, {})
                    strategies = task.get("strategies", {})
                    st = strategies.get("start_time")
                    et = strategies.get("end_time")
                    now = int(time.time())

                    if st is not None and et is not None:
                        if not (st < now < et):
                            self.log(f"单元 {node_id} 不在时间范围内")
                            continue

                # 获取单元任务详情（含必修状态、完成状态）
                task_status_list = self.fetch_unit_task_detail(node_id, course_resource_id)
                if not task_status_list or not task_status_list[0].get("required", False):
                    self.log(f"单元 {node_id} 非必修，跳过")
                    continue

                # 遍历支持的任务
                for task_id in supported:
                    # 本地已提交或 API 返回已完成，均跳过
                    if task_id in submitted_task_ids or self.is_task_completed(task_status_list, task_id):
                        self.log(f"任务 {task_id} 已完成，跳过")
                        continue

                    self.log(f"处理任务: {task_id}, 类型: {supported[task_id]}")

                    # 获取答案（使用 tutorialId 而非 instantId）
                    answer_data, answer_key = self.fetch_task_answer(tutorial_id, task_id)
                    if not answer_data:
                        self.log(f"任务 {task_id} 无答案数据")
                        continue

                    # 解密并构造请求体
                    decrypted = decrypt_aes(answer_data, answer_key)
                    instance_id, answers = extract_answers(decrypted)
                    question_type = supported[task_id]

                    try:
                        body = build_submit_body(
                            instance_id, answers, task_id,
                            instant_id, self.openid, question_type
                        )
                    except ValueError as e:
                        self.log(f"构造请求体失败: {e}")
                        continue

                    # 提交答案
                    success, resp_data = self.submit_answer(body)
                    if success:
                        self.log(f"任务 {task_id} 提交成功")
                        has_done_any = True
                        task_count += 1
                        submitted_task_ids.add(task_id)
                    else:
                        self.log(f"任务 {task_id} 提交失败: {resp_data}")

                    # 额外请求
                    if resp_data.get("data") and resp_data["data"].get("record_grade"):
                        ts = resp_data["data"]["record_grade"].get("ts", int(time.time()))
                        self.do_extra_request(course_id or instant_id, task_id, ts)

                    # 随机停顿 30-60 秒
                    sleep_time = random.randint(30, 60)
                    self.log(f"停顿 {sleep_time} 秒...")
                    time.sleep(sleep_time)

        # 记录最终进度
        if all_resource_ids:
            final_data = self.fetch_unit_situation(all_resource_ids[0]["id"])
            if final_data and is_api_success(final_data):
                self.final_progress = final_data.get("value", {}).get("totalDetail", {})
                result["final_progress"] = self.final_progress

                if self.initial_progress and self.final_progress:
                    delta = {
                        "timeDelta": self.final_progress.get("duration", 0) - self.initial_progress.get("duration", 0),
                        "scoreDelta": self.final_progress.get("score", 0) - self.initial_progress.get("score", 0),
                        "progressDelta": self.final_progress.get("finishProgress", 0) - self.initial_progress.get("finishProgress", 0)
                    }
                    result["delta"] = delta
                    self.log(f"进度变化: 时间+{delta['timeDelta']}秒, 分数+{delta['scoreDelta']}, 进度+{delta['progressDelta']}%")

        # 通知
        if has_done_any:
            result["success"] = True
            result["message"] = "任务完成"
            notify_order_completed(self.email, progress=result.get("delta"), task_count=task_count)
            notify_admin("U校园AI循环任务完成", f"账号: {self.phone}\n完成任务: {task_count} 个", user_info=self.user_info, logs=self.logs)
        else:
            result["message"] = "没有可做的任务"
            reason = "当前课程可能不在开放时间内，或所有任务已完成。"
            notify_unavailable(self.email, courses=courses, reason=reason)
            notify_admin("不可做提醒", f"账号: {self.phone}\n{reason}", user_info=self.user_info, logs=self.logs)

        result["logs"] = self.logs
        return result
