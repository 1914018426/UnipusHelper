import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import settings

STYLE = """
<style>
body { font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #F5F3FF; margin: 0; padding: 24px; color: #1E1B4B; }
.container { max-width: 600px; margin: 0 auto; background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(99,102,241,0.08); }
.header { background: #6366F1; padding: 32px 24px; text-align: center; }
.header h1 { color: #fff; margin: 0; font-size: 22px; font-weight: 700; }
.header p { color: rgba(255,255,255,0.85); margin: 8px 0 0; font-size: 14px; }
.body { padding: 28px 24px; }
.section { margin-bottom: 20px; }
.section-title { font-size: 13px; font-weight: 600; color: #6366F1; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
.card { background: #F5F3FF; border-radius: 10px; padding: 16px; }
.card-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #E0E7FF; font-size: 14px; }
.card-row:last-child { border-bottom: none; }
.label { color: #64748B; }
.value { font-weight: 600; color: #1E1B4B; }
.status-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.status-success { background: #D1FAE5; color: #047857; }
.status-fail { background: #FEE2E2; color: #B91C1C; }
.status-warn { background: #FEF3C7; color: #B45309; }
.btn { display: inline-block; background: #6366F1; color: #fff; padding: 12px 28px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px; }
.footer { text-align: center; padding: 20px 24px; background: #FAFAFA; font-size: 12px; color: #94A3B8; }
.progress-bar { width: 100%; height: 8px; background: #E0E7FF; border-radius: 4px; overflow: hidden; margin-top: 8px; }
.progress-fill { height: 100%; background: #10B981; border-radius: 4px; }
.course-list { list-style: none; padding: 0; margin: 0; }
.course-item { display: flex; align-items: center; padding: 10px 0; border-bottom: 1px solid #E0E7FF; font-size: 14px; }
.course-item:last-child { border-bottom: none; }
.course-icon { width: 32px; height: 32px; background: #6366F1; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; color: #fff; font-size: 14px; font-weight: 700; margin-right: 12px; }
</style>
"""

def _build_html(title, content_html):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">{STYLE}</head>
<body>
<div class="container">
  <div class="header"><h1>UnipusHelper Pro</h1><p>{title}</p></div>
  <div class="body">{content_html}</div>
  <div class="footer">本邮件由 UnipusHelper Pro 自动发送，请勿直接回复</div>
</div>
</body></html>"""

def send_email(to_email: str, subject: str, body: str, is_html: bool = False):
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        print(f"[Email] SMTP not configured, skipping: {subject}")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = settings.SMTP_FROM or settings.SMTP_USER
        msg['To'] = to_email
        msg['Subject'] = subject
        content_type = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, content_type, 'utf-8'))
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT)
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(msg['From'], to_email, msg.as_string())
        server.quit()
        print(f"[Email] Sent to {to_email}: {subject}")
        return True
    except Exception as e:
        print(f"[Email Error] {e}")
        return False

def notify_login_success(to_email: str, user_info: dict = None, courses: list = None):
    user_name = user_info.get('name', '同学') if user_info else '同学'
    school = user_info.get('schName', '') if user_info else ''
    course_rows = ""
    if courses:
        for c in courses[:5]:
            course_rows += f'<li class="course-item"><span class="course-icon">课</span><span>{c.get("name", "未知课程")}</span></li>'
    else:
        course_rows = '<li class="course-item"><span style="color:#94A3B8">暂无课程信息</span></li>'

    html = _build_html("登录成功", f"""
    <div class="section">
      <span class="status-badge status-success">登录成功</span>
    </div>
    <div class="section">
      <div class="section-title">用户信息</div>
      <div class="card">
        <div class="card-row"><span class="label">姓名</span><span class="value">{user_name}</span></div>
        <div class="card-row"><span class="label">学校</span><span class="value">{school}</span></div>
      </div>
    </div>
    <div class="section">
      <div class="section-title">已发现课程</div>
      <ul class="course-list">{course_rows}</ul>
    </div>
    <div class="section" style="text-align:center; margin-top:28px;">
      <p style="color:#64748B; font-size:14px; margin-bottom:16px;">系统正在自动处理学习任务，完成后将发送通知。</p>
    </div>
    """)
    send_email(to_email, "U校园AI - 登录成功", html, is_html=True)

def notify_login_failed(to_email: str, phone: str = "", error: str = ""):
    html = _build_html("登录失败", f"""
    <div class="section">
      <span class="status-badge status-fail">登录失败</span>
    </div>
    <div class="section">
      <div class="section-title">失败详情</div>
      <div class="card">
        <div class="card-row"><span class="label">账号</span><span class="value">{phone or '未知'}</span></div>
        <div class="card-row"><span class="label">原因</span><span class="value" style="color:#B91C1C">{error or '账号或密码错误'}</span></div>
      </div>
    </div>
    <div class="section" style="text-align:center; margin-top:20px;">
      <p style="color:#64748B; font-size:14px;">请检查账号密码是否正确，然后重新提交任务。</p>
    </div>
    """)
    send_email(to_email, "U校园AI - 登录失败", html, is_html=True)

def notify_order_completed(to_email: str, progress: dict = None, task_count: int = 0):
    score_delta = progress.get('scoreDelta', 0) if progress else 0
    progress_delta = progress.get('progressDelta', 0) if progress else 0

    html = _build_html("任务完成", f"""
    <div class="section">
      <span class="status-badge status-success">任务完成</span>
    </div>
    <div class="section">
      <div class="section-title">执行结果</div>
      <div class="card">
        <div class="card-row"><span class="label">提交任务数</span><span class="value">{task_count} 个</span></div>
        <div class="card-row"><span class="label">分数变化</span><span class="value">+{score_delta:.1f} 分</span></div>
        <div class="card-row"><span class="label">完成进度变化</span><span class="value">+{progress_delta:.1f}%</span></div>
      </div>
    </div>
    <div class="section" style="text-align:center; margin-top:28px;">
      <p style="color:#64748B; font-size:14px; margin-bottom:16px;">请登录 U校园AI 查看详细学习进度。</p>
      <a href="https://uai.unipus.cn" class="btn" style="color:#fff">前往 U校园AI</a>
    </div>
    """)
    send_email(to_email, "U校园AI - 任务完成", html, is_html=True)

def notify_unavailable(to_email: str, courses: list = None, reason: str = ""):
    course_rows = ""
    if courses:
        for c in courses[:5]:
            course_rows += f'<li class="course-item"><span class="course-icon">课</span><span>{c.get("name", "未知课程")}</span></li>'
    else:
        course_rows = '<li class="course-item"><span style="color:#94A3B8">未获取到课程</span></li>'

    html = _build_html("无可执行任务", f"""
    <div class="section">
      <span class="status-badge status-warn">无可执行</span>
    </div>
    <div class="section">
      <div class="section-title">原因</div>
      <div class="card">
        <p style="margin:0; font-size:14px; color:#1E1B4B;">{reason or '当前课程可能不在开放时间内，或所有任务已完成。'}</p>
      </div>
    </div>
    <div class="section">
      <div class="section-title">检测到的课程</div>
      <ul class="course-list">{course_rows}</ul>
    </div>
    <div class="section" style="text-align:center; margin-top:20px;">
      <p style="color:#64748B; font-size:14px;">建议检查课程是否在开放时间内，或联系管理员协助。</p>
    </div>
    """)
    send_email(to_email, "U校园AI - 无可执行任务", html, is_html=True)

def notify_admin(subject: str, body: str, user_info: dict = None, logs: list = None):
    if not settings.ADMIN_EMAIL:
        return False
    log_text = "<br>".join(logs[-30:]) if logs else "无日志"
    user_detail = ""
    if user_info:
        user_detail = f"<p><strong>用户:</strong> {user_info.get('name', '未知')} ({user_info.get('phone', '')})</p>"
    html = _build_html("管理员通知", f"""
    <div class="section">
      <div class="section-title">{subject}</div>
      <p style="font-size:14px; color:#1E1B4B;">{body}</p>
      {user_detail}
    </div>
    <div class="section">
      <div class="section-title">最近日志</div>
      <div class="card" style="font-family:monospace; font-size:12px; color:#475569; white-space:pre-wrap;">{log_text}</div>
    </div>
    """)
    send_email(settings.ADMIN_EMAIL, f"[Admin] {subject}", html, is_html=True)
