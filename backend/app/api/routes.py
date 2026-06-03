import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from typing import List, Optional
import jwt as pyjwt
from datetime import datetime, timedelta

from app.models import Task, User, engine
from app.schemas import (
    TaskCreate, TaskResponse, TaskStatus,
    UserRegister, UserLogin, UserResponse
)
from app.config import settings
from app.services.email import (
    notify_login_success, notify_login_failed,
    notify_order_completed, notify_unavailable, notify_admin
)
from app.services.crypto import encrypt_password, decrypt_password

router = APIRouter()

JWT_SECRET = settings.UNIPUS_SECRET
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 168  # 7 days

def get_db():
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def create_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4())
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = None
    # 1. Check Authorization header
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    # 2. Check cookie
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload.get("sub", 0))
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户不存在")
        return user
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期")
    except Exception:
        raise HTTPException(status_code=401, detail="无效的登录凭证")

# ========== Auth ==========
@router.post("/auth/register", response_model=UserResponse)
def register(user: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == user.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="邮箱已被注册")
    db_user = User(email=user.email)
    db_user.set_password(user.password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.post("/auth/login")
def login(
    user: UserLogin,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    db_user = db.query(User).filter(User.email == user.email).first()
    if not db_user or not db_user.check_password(user.password):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    token = create_token(db_user.id)
    # 根据实际访问协议（由 nginx 透传）决定 secure 标志
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    secure_cookie = settings.USE_HTTPS and proto == "https"
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        max_age=JWT_EXPIRE_HOURS * 3600,
        path="/"
    )
    return {"token": token, "user": {"id": db_user.id, "email": db_user.email, "default_phone": db_user.default_phone}}

@router.post("/auth/logout")
def logout(request: Request, response: Response):
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    secure_cookie = settings.USE_HTTPS and proto == "https"
    response.delete_cookie(key="access_token", path="/", secure=secure_cookie)
    return {"message": "已退出登录"}

@router.get("/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user

# ========== Tasks ==========
@router.post("/tasks", response_model=TaskResponse)
def create_task(
    task: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"[create_task] user_id={current_user.id}, default_phone={current_user.default_phone!r}, task.phone={task.phone!r}")

    if current_user.default_phone and current_user.default_phone != task.phone:
        logger.warning(f"[create_task] 拒绝：用户 {current_user.id} 尝试用 {task.phone} 替换已绑定的 {current_user.default_phone}")
        raise HTTPException(status_code=400, detail=f"账号已锁定为 {current_user.default_phone}，不可更换")

    # 首次使用即绑定手机号，持久化到数据库
    if not current_user.default_phone:
        current_user.default_phone = task.phone
        db.commit()
        logger.info(f"[create_task] 绑定手机号：用户 {current_user.id} -> {task.phone}")

    # 限制：每个用户同时只能有一个 running/queued 的任务
    active = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.status.in_(["running", "queued"])
    ).first()
    if active:
        raise HTTPException(status_code=400, detail="已有任务在执行中，请等待完成后再提交")

    db_task = Task(
        user_id=current_user.id,
        email=current_user.email,
        phone=task.phone,
        password=encrypt_password(task.password),
        status="pending",
        progress=0.0,
        log="任务已创建，等待执行..."
    )
    db.add(db_task)
    db.commit()
    db.refresh(db_task)

    try:
        from app.tasks.celery_app import process_order_task
        celery_task = process_order_task.delay(db_task.id)
        db_task.celery_task_id = celery_task.id
        db_task.status = "queued"
        db_task.log = "任务已加入队列..."
        db.commit()
    except Exception as e:
        db_task.log = f"队列提交失败: {e}，将同步执行"
        db.commit()
        run_task_sync(db_task.id, db)

    return db_task

@router.get("/tasks", response_model=List[TaskResponse])
def list_tasks(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return (
        db.query(Task)
        .filter(Task.user_id == current_user.id)
        .order_by(Task.id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

@router.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task

@router.get("/tasks/{task_id}/status", response_model=TaskStatus)
def get_task_status(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return TaskStatus(
        id=task.id,
        status=task.status,
        progress=task.progress,
        log=task.log
    )

@router.post("/tasks/{task_id}/run")
def run_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status == "running":
        raise HTTPException(status_code=400, detail="任务正在执行中")

    # 限制：每个用户同时只能有一个 running/queued 的任务
    active = db.query(Task).filter(
        Task.user_id == current_user.id,
        Task.status.in_(["running", "queued"]),
        Task.id != task_id
    ).first()
    if active:
        raise HTTPException(status_code=400, detail="已有其他任务在执行中，请等待完成后再提交")

    try:
        from app.tasks.celery_app import process_order_task
        celery_task = process_order_task.delay(task_id)
        task.celery_task_id = celery_task.id
        task.status = "queued"
        task.log = "任务已重新加入队列..."
        db.commit()
        return {"message": "任务已重新加入队列", "task_id": task_id}
    except Exception as e:
        run_task_sync(task_id, db)
        return {"message": "任务已同步执行", "task_id": task_id}

@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = db.query(Task).filter(
        Task.id == task_id,
        Task.user_id == current_user.id
    ).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Revoke celery task if exists
    if task.celery_task_id:
        try:
            from app.tasks.celery_app import celery_app
            celery_app.control.revoke(task.celery_task_id, terminate=True)
        except Exception:
            pass

    if task.status in ["running", "queued"]:
        task.status = "failed"
        task.log += "\n[用户取消] 任务已被中止"
        db.commit()
        return {"message": "任务已中止", "task_id": task_id}
    else:
        raise HTTPException(status_code=400, detail="当前状态不可中止")

def run_task_sync(task_id: int, db: Session):
    from sqlalchemy.orm import sessionmaker
    from app.services.unipus import UnipusClient
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return
        task.status = "running"
        task.log = "开始执行..."
        db.commit()

        client = UnipusClient(
            phone=task.phone,
            password=decrypt_password(task.password),
            email=task.email
        )
        result = client.run()

        task.log = "\n".join(client.logs)
        if result.get("success"):
            task.status = "completed"
            task.progress = 100.0
        else:
            task.status = "failed"
            task.progress = 0.0
        db.commit()
    except Exception as e:
        db.rollback()
        task = db.query(Task).filter(Task.id == task_id).first()
        if task:
            task.status = "failed"
            task.log += f"\n[ERROR] {str(e)}"
            db.commit()
    finally:
        db.close()
