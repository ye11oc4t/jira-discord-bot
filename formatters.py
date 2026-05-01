"""
Jira Webhook → Discord Embed 포맷터
지원 이벤트: 이슈, 댓글, 스프린트, 버전(릴리즈), 워크로그, 프로젝트
"""

# Discord 색상
COLOR = {
    "created":    0x2ECC71,
    "updated":    0x3498DB,
    "deleted":    0xE74C3C,
    "done":       0x1ABC9C,
    "todo":       0x95A5A6,
    "inprogress": 0xF39C12,
    "started":    0x2ECC71,
    "closed":     0xE74C3C,
    "released":   0x9B59B6,
    "default":    0x7289DA,
}

JIRA_ICON = "https://wac-cdn.atlassian.com/assets/img/favicons/jira/favicon.png"


def _color(key: str) -> int:
    return COLOR.get(key.lower().replace(" ", ""), COLOR["default"])


def _safe(val, fallback="(없음)") -> str:
    return str(val) if val else fallback


def _issue_url(payload: dict) -> str:
    issue = payload.get("issue", {})
    base = issue.get("self", "").split("/rest/")[0]
    key = issue.get("key", "")
    return f"{base}/browse/{key}" if base and key else ""


def _get_avatar(user_obj: dict) -> str:
    return (user_obj or {}).get("avatarUrls", {}).get("48x48", JIRA_ICON)


def _issue_fields(issue: dict) -> dict:
    f = issue.get("fields", {})
    return {
        "key":        issue.get("key", "?"),
        "summary":    f.get("summary", "(제목 없음)"),
        "status":     f.get("status", {}).get("name", "?"),
        "priority":   f.get("priority", {}).get("name", "?"),
        "issuetype":  f.get("issuetype", {}).get("name", "?"),
        "assignee":   (f.get("assignee") or {}).get("displayName", "미배정"),
        "reporter":   (f.get("reporter") or {}).get("displayName", "?"),
        "project":    f.get("project", {}).get("name", "?"),
        "description": (f.get("description") or "")[:200] or "(설명 없음)",
        "labels":     ", ".join(f.get("labels") or []) or "없음",
    }


def fmt_issue_created(payload: dict) -> dict:
    issue = payload.get("issue", {})
    f = _issue_fields(issue)
    url = _issue_url(payload)
    user_obj = payload.get("user") or {}
    user = user_obj.get("displayName", "?")

    return {
        "title": f"🆕 이슈 생성 · {f['key']}",
        "description": f"**[{f['summary']}]({url})**",
        "color": _color("created"),
        "fields": [
            {"name": "유형",     "value": f["issuetype"],   "inline": True},
            {"name": "우선순위", "value": f["priority"],    "inline": True},
            {"name": "상태",     "value": f["status"],      "inline": True},
            {"name": "담당자",   "value": f["assignee"],    "inline": True},
            {"name": "보고자",   "value": f["reporter"],    "inline": True},
            {"name": "프로젝트", "value": f["project"],     "inline": True},
            {"name": "설명",     "value": f["description"], "inline": False},
            {"name": "레이블",   "value": f["labels"],      "inline": False},
        ],
        "footer": {"text": f"생성: {user}", "icon_url": _get_avatar(user_obj)},
    }


def fmt_issue_deleted(payload: dict) -> dict:
    issue = payload.get("issue", {})
    f = _issue_fields(issue)
    user_obj = payload.get("user") or {}
    user = user_obj.get("displayName", "?")

    return {
        "title": f"🗑️ 이슈 삭제 · {f['key']}",
        "description": f"**{f['summary']}** 이슈가 삭제되었습니다.",
        "color": _color("deleted"),
        "fields": [
            {"name": "프로젝트", "value": f["project"],   "inline": True},
            {"name": "유형",     "value": f["issuetype"], "inline": True},
        ],
        "footer": {"text": f"삭제: {user}", "icon_url": _get_avatar(user_obj)},
    }


def fmt_issue_status_changed(payload: dict) -> dict:
    issue = payload.get("issue", {})
    f = _issue_fields(issue)
    url = _issue_url(payload)
    user_obj = payload.get("user") or {}
    user = user_obj.get("displayName", "?")

    from_status, to_status = "?", f["status"]
    for item in payload.get("changelog", {}).get("items", []):
        if item.get("field") == "status":
            from_status = _safe(item.get("fromString"))
            to_status   = _safe(item.get("toString"))

    status_lower = to_status.lower().replace(" ", "")
    color = _color(status_lower)

    icons = {
        "done": "✅", "inprogress": "🔄", "todo": "📋",
        "closed": "🔒", "reopened": "🔓",
    }
    icon = icons.get(status_lower, "🔀")

    return {
        "title": f"{icon} 상태 변경 · {f['key']}",
        "description": f"**[{f['summary']}]({url})**\n`{from_status}` → `{to_status}`",
        "color": color,
        "fields": [
            {"name": "담당자",   "value": f["assignee"], "inline": True},
            {"name": "우선순위", "value": f["priority"],  "inline": True},
            {"name": "프로젝트", "value": f["project"],   "inline": True},
        ],
        "footer": {"text": f"변경: {user}", "icon_url": _get_avatar(user_obj)},
    }


def fmt_comment(payload: dict, action: str) -> dict:
    issue = payload.get("issue", {})
    f = _issue_fields(issue)
    url = _issue_url(payload)
    comment = payload.get("comment", {})
    body = (comment.get("body") or "")[:300] or "(내용 없음)"
    author_obj = comment.get("author") or {}
    author = author_obj.get("displayName", "?")

    icons = {"created": "💬", "updated": "📝", "deleted": "🗑️"}
    colors = {"created": "created", "updated": "updated", "deleted": "deleted"}

    return {
        "title": f"{icons.get(action,'💬')} 댓글 {action} · {f['key']}",
        "description": f"**[{f['summary']}]({url})**\n\n> {body}",
        "color": _color(colors.get(action, "default")),
        "fields": [
            {"name": "작성자",   "value": author,       "inline": True},
            {"name": "프로젝트", "value": f["project"], "inline": True},
        ],
        "footer": {"text": f"댓글: {author}", "icon_url": _get_avatar(author_obj)},
    }


def fmt_sprint(payload: dict) -> dict:
    sprint = payload.get("sprint", {})
    name  = sprint.get("name", "?")
    state = sprint.get("state", "?")
    goal  = sprint.get("goal") or "(목표 없음)"
    start = sprint.get("startDate", "?")
    end   = sprint.get("endDate", "?")
    event = payload.get("webhookEvent", "")

    action_map = {
        "sprint_created": ("🆕 스프린트 생성", "created"),
        "sprint_started": ("🚀 스프린트 시작", "started"),
        "sprint_closed":  ("🏁 스프린트 종료", "closed"),
        "sprint_updated": ("✏️ 스프린트 수정", "updated"),
        "sprint_deleted": ("🗑️ 스프린트 삭제", "deleted"),
    }
    title_str, color_key = action_map.get(event, ("🔔 스프린트 이벤트", "default"))

    return {
        "title": f"{title_str} · {name}",
        "description": f"**목표**: {goal}",
        "color": _color(color_key),
        "fields": [
            {"name": "상태",   "value": state, "inline": True},
            {"name": "시작일", "value": start, "inline": True},
            {"name": "종료일", "value": end,   "inline": True},
        ],
        "footer": {"text": "Jira Sprint", "icon_url": JIRA_ICON},
    }


def fmt_version(payload: dict) -> dict:
    version = payload.get("version", {})
    name         = version.get("name", "?")
    description  = (version.get("description") or "")[:200] or "(설명 없음)"
    released     = version.get("released", False)
    release_date = version.get("releaseDate", "?")
    project      = version.get("projectId", "?")
    event        = payload.get("webhookEvent", "")

    action_map = {
        "jira:version_created":    ("🆕 버전 생성",     "created"),
        "jira:version_updated":    ("✏️ 버전 수정",     "updated"),
        "jira:version_deleted":    ("🗑️ 버전 삭제",     "deleted"),
        "jira:version_released":   ("🚀 버전 릴리즈",   "released"),
        "jira:version_unreleased": ("↩️ 릴리즈 취소",  "updated"),
        "jira:version_moved":      ("🔀 버전 이동",     "updated"),
        "jira:version_archived":   ("📦 버전 아카이브", "deleted"),
    }
    title_str, color_key = action_map.get(event, ("🔔 버전 이벤트", "default"))

    return {
        "title": f"{title_str} · {name}",
        "description": description,
        "color": _color(color_key),
        "fields": [
            {"name": "릴리즈됨",    "value": "✅" if released else "❌", "inline": True},
            {"name": "릴리즈 날짜", "value": release_date,               "inline": True},
            {"name": "프로젝트 ID", "value": str(project),               "inline": True},
        ],
        "footer": {"text": "Jira Version", "icon_url": JIRA_ICON},
    }


def fmt_worklog(payload: dict) -> dict:
    worklog    = payload.get("worklog", {})
    issue      = worklog.get("issue", {})
    issue_key  = issue.get("key") or "?"
    author     = (worklog.get("author") or {}).get("displayName", "?")
    time_spent = worklog.get("timeSpent", "?")
    comment    = (worklog.get("comment") or "")[:200] or "(메모 없음)"
    event      = payload.get("webhookEvent", "")

    action_map = {
        "worklog_created": ("⏱️ 워크로그 추가", "created"),
        "worklog_updated": ("✏️ 워크로그 수정", "updated"),
        "worklog_deleted": ("🗑️ 워크로그 삭제", "deleted"),
    }
    title_str, color_key = action_map.get(event, ("⏱️ 워크로그", "default"))

    return {
        "title": f"{title_str} · {issue_key}",
        "description": f"> {comment}",
        "color": _color(color_key),
        "fields": [
            {"name": "작업자",    "value": author,     "inline": True},
            {"name": "소요 시간", "value": time_spent, "inline": True},
        ],
        "footer": {"text": "Jira Worklog", "icon_url": JIRA_ICON},
    }


def fmt_project(payload: dict) -> dict:
    project = payload.get("project", {})
    name    = project.get("name", "?")
    key     = project.get("key", "?")
    ptype   = project.get("projectTypeKey", "?")
    lead    = (project.get("projectLead") or {}).get("displayName", "?")
    event   = payload.get("webhookEvent", "")

    action_map = {
        "project_created":          ("🆕 프로젝트 생성",  "created"),
        "project_updated":          ("✏️ 프로젝트 수정",  "updated"),
        "project_deleted":          ("🗑️ 프로젝트 삭제",  "deleted"),
        "project_soft_deleted":     ("📦 프로젝트 휴지통","deleted"),
        "project_restored_deleted": ("↩️ 프로젝트 복원",  "created"),
    }
    title_str, color_key = action_map.get(event, ("🔔 프로젝트 이벤트", "default"))

    return {
        "title": f"{title_str} · {name}",
        "description": f"프로젝트 키: `{key}`",
        "color": _color(color_key),
        "fields": [
            {"name": "유형", "value": ptype, "inline": True},
            {"name": "리드", "value": lead,  "inline": True},
        ],
        "footer": {"text": "Jira Project", "icon_url": JIRA_ICON},
    }


def fmt_user(payload: dict) -> dict:
    user  = payload.get("user", {})
    name  = user.get("displayName", "?")
    email = user.get("emailAddress", "?")
    event = payload.get("webhookEvent", "")

    action_map = {
        "user_created": ("👤 사용자 생성", "created"),
        "user_updated": ("✏️ 사용자 수정", "updated"),
        "user_deleted": ("🗑️ 사용자 삭제", "deleted"),
    }
    title_str, color_key = action_map.get(event, ("👤 사용자 이벤트", "default"))

    return {
        "title": title_str,
        "description": f"**{name}** (`{email}`)",
        "color": _color(color_key),
        "footer": {"text": "Jira User", "icon_url": JIRA_ICON},
    }


def fmt_board(payload: dict) -> dict:
    board = payload.get("board", {})
    name  = board.get("name", "?")
    btype = board.get("type", "?")
    event = payload.get("webhookEvent", "")

    action_map = {
        "board_created":               ("📋 보드 생성",      "created"),
        "board_updated":               ("✏️ 보드 수정",      "updated"),
        "board_deleted":               ("🗑️ 보드 삭제",      "deleted"),
        "board_configuration_changed": ("⚙️ 보드 설정 변경", "updated"),
    }
    title_str, color_key = action_map.get(event, ("📋 보드 이벤트", "default"))

    return {
        "title": f"{title_str} · {name}",
        "description": f"보드 유형: `{btype}`",
        "color": _color(color_key),
        "footer": {"text": "Jira Board", "icon_url": JIRA_ICON},
    }


def format_event(event_type: str, payload: dict):
    if event_type == "jira:issue_created":
        return fmt_issue_created(payload)

    if event_type == "jira:issue_deleted":
        return fmt_issue_deleted(payload)

    if event_type == "jira:issue_updated":
        return None  # 이슈 수정 알림 비활성화

    if event_type in ("comment_created", "comment_updated", "comment_deleted"):
        action = event_type.split("_")[1]
        return fmt_comment(payload, action)

    if event_type in ("sprint_created", "sprint_started", "sprint_closed",
                      "sprint_updated", "sprint_deleted"):
        return fmt_sprint(payload)

    if event_type in ("jira:version_created", "jira:version_updated",
                      "jira:version_deleted", "jira:version_released",
                      "jira:version_unreleased", "jira:version_moved",
                      "jira:version_archived"):
        return fmt_version(payload)

    if event_type in ("worklog_created", "worklog_updated", "worklog_deleted"):
        return fmt_worklog(payload)

    if event_type in ("project_created", "project_updated", "project_deleted",
                      "project_soft_deleted", "project_restored_deleted"):
        return fmt_project(payload)

    if event_type in ("user_created", "user_updated", "user_deleted"):
        return fmt_user(payload)

    if event_type in ("board_created", "board_updated", "board_deleted",
                      "board_configuration_changed"):
        return fmt_board(payload)

    return None
