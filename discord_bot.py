import os
import discord
from discord import app_commands
from datetime import datetime, timezone, timedelta
import httpx
import redis.asyncio as aioredis
import json
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JIRA_BASE_URL     = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL        = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN    = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY  = os.getenv("JIRA_PROJECT_KEY", "MADI")
REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379")

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)

redis_client = None

KST = timezone(timedelta(hours=9))


async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


def get_jira_auth():
    import base64
    encoded = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}


def fmt_time(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%H:%M")


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%m/%d %H:%M")


def fmt_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total = int(delta.total_seconds())
    h, m = divmod(total // 60, 60)
    if h > 0:
        return f"{h}시간 {m}분"
    return f"{m}분"


def gantt_bar(start: datetime, end: datetime | None, day_start: datetime, total_mins: int = 480) -> str:
    """하루 8시간 기준 블록 12칸 간트바"""
    s = max(0, (start - day_start).total_seconds() / 60)
    e = (end - day_start).total_seconds() / 60 if end else (datetime.now(timezone.utc) - day_start).total_seconds() / 60
    e = min(e, total_mins)
    s = min(s, total_mins)
    bar_len = 12
    filled_start = int(s / total_mins * bar_len)
    filled_len   = max(1, int((e - s) / total_mins * bar_len))
    filled_end   = min(bar_len, filled_start + filled_len)
    bar = "░" * filled_start + "█" * (filled_end - filled_start) + "░" * (bar_len - filled_end)
    return bar


async def get_issue(issue_key: str):
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}",
            headers=get_jira_auth(),
            params={"fields": "summary,status,assignee,transitions"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None, f"이슈 없음: {issue_key}"
        return resp.json(), None


async def fetch_issues(jql: str, fields: str = "summary,status,assignee"):
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{JIRA_BASE_URL}/rest/api/3/search/jql",
            headers=get_jira_auth(),
            params={"jql": jql, "maxResults": 30, "fields": fields},
            timeout=10,
        )
        if resp.status_code != 200:
            return None, f"Jira API 오류: {resp.status_code}"
        return resp.json(), None


async def get_transitions(issue_key: str):
    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions",
            headers=get_jira_auth(),
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("transitions", [])


async def do_transition(issue_key: str, status_name: str) -> bool:
    transitions = await get_transitions(issue_key)
    if not transitions:
        return False
    target = None
    for t in transitions:
        if t["to"]["name"].lower() == status_name.lower():
            target = t["id"]
            break
    if not target:
        for t in transitions:
            if status_name.lower() in t["to"]["name"].lower():
                target = t["id"]
                break
    if not target:
        return False
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{JIRA_BASE_URL}/rest/api/3/issue/{issue_key}/transitions",
            headers=get_jira_auth(),
            json={"transition": {"id": target}},
            timeout=10,
        )
        return resp.status_code == 204


# ── /task ──────────────────────────────────────────────────────────────────────

task_group = app_commands.Group(name="task", description="Jira 태스크 관리")


@task_group.command(name="start", description="태스크 시작")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)", assignee="담당자 멘션")
async def task_start(interaction: discord.Interaction, issue_key: str, assignee: discord.Member):
    await interaction.response.defer()

    issue_key = issue_key.upper()
    issue, error = await get_issue(issue_key)
    if error:
        await interaction.followup.send(f"❌ {error}")
        return

    summary      = issue["fields"]["summary"]
    current_status = issue["fields"]["status"]["name"]

    if current_status == "완료":
        # 완료된 작업 재시작 확인
        view = RestartConfirmView(issue_key, summary, assignee)
        await interaction.followup.send(
            f"⚠️ **{issue_key}**는 이미 완료된 작업입니다. 다시 진행하시겠습니까?",
            view=view,
        )
        return

    if current_status != "진행 중":
        success = await do_transition(issue_key, "진행 중")
        if not success:
            await interaction.followup.send(f"❌ In Progress 전환 실패")
            return

    r   = await get_redis()
    now = datetime.now(timezone.utc)
    await r.set(f"task_active:{issue_key}", json.dumps({
        "issue_key": issue_key,
        "summary":   summary,
        "user":      assignee.display_name,
        "user_id":   assignee.id,
        "start":     now.isoformat(),
    }), ex=86400)

    await interaction.followup.send(
        f"🚩 task `{issue_key}` {summary}\n"
        f"**{assignee.mention}** | {fmt_dt(now)}"
    )


@task_group.command(name="status", description="진행 중인 태스크 목록")
async def task_status(interaction: discord.Interaction):
    await interaction.response.defer()
    r    = await get_redis()
    keys = await r.keys("task_active:*")

    if not keys:
        await interaction.followup.send("현재 진행 중인 태스크가 없어요.")
        return

    lines = []
    for k in keys:
        raw = await r.get(k)
        if not raw:
            continue
        d        = json.loads(raw)
        start_dt = datetime.fromisoformat(d["start"])
        elapsed  = fmt_duration(start_dt, datetime.now(timezone.utc))
        lines.append(
            f"🟢 `{d['issue_key']}` {d['summary']}\n"
            f"　👤 {d['user']} | {fmt_dt(start_dt)} 시작 ({elapsed} 경과)"
        )

    embed = discord.Embed(
        title="🟢 진행 중인 태스크",
        description="\n\n".join(lines),
        color=0x34A853,
    )
    await interaction.followup.send(embed=embed)


@task_group.command(name="done", description="태스크 완료")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)")
async def task_done(interaction: discord.Interaction, issue_key: str):
    await _finish_task(interaction, issue_key, "done")


@task_group.command(name="stop", description="태스크 중단")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)")
async def task_stop(interaction: discord.Interaction, issue_key: str):
    await _finish_task(interaction, issue_key, "stop")


@task_group.command(name="today", description="오늘 작업 현황 간트차트")
async def task_today(interaction: discord.Interaction):
    await interaction.response.defer()
    r     = await get_redis()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    logs_raw = await r.lrange(f"work_log:{today}", 0, -1)

    # 진행중인 것도 포함
    active_keys = await r.keys("task_active:*")
    active_logs = []
    for k in active_keys:
        raw = await r.get(k)
        if raw:
            d = json.loads(raw)
            active_logs.append({**d, "end": None, "action": "active"})

    logs = [json.loads(l) for l in logs_raw] + active_logs

    if not logs:
        await interaction.followup.send("오늘 기록된 작업이 없어요.")
        return

    # 오늘 09:00 KST 기준
    day_start = datetime.now(KST).replace(hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

    user_logs: dict[str, list] = {}
    for log in logs:
        u = log.get("user", "?")
        user_logs.setdefault(u, []).append(log)

    lines = [f"📊 **{today} 작업 현황**\n"]
    for user, ulogs in user_logs.items():
        lines.append(f"👤 **{user}**")
        for log in sorted(ulogs, key=lambda x: x.get("start", "")):
            issue_key = log.get("issue_key", "?")
            summary   = log.get("summary", "")[:20]
            start_dt  = datetime.fromisoformat(log["start"]) if log.get("start") else None
            end_dt    = datetime.fromisoformat(log["end"]) if log.get("end") else None
            action    = log.get("action", "done")

            if start_dt:
                bar  = gantt_bar(start_dt, end_dt, day_start)
                time_str = f"{fmt_time(start_dt)}~{fmt_time(end_dt) if end_dt else '진행중'}"
                if action == "active":
                    icon = "🟢"
                elif action == "done":
                    icon = "✅"
                else:
                    icon = "⏹️"
                duration = fmt_duration(start_dt, end_dt or datetime.now(timezone.utc))
                lines.append(f"{icon} `{issue_key}` `{bar}` {time_str} ({duration})")
        lines.append("")

    await interaction.followup.send("\n".join(lines))


async def _finish_task(interaction: discord.Interaction, issue_key: str, action: str):
    await interaction.response.defer()
    issue_key = issue_key.upper()
    r   = await get_redis()
    now = datetime.now(timezone.utc)

    task_raw = await r.get(f"task_active:{issue_key}")
    summary  = ""
    start_dt = None
    user     = interaction.user.display_name

    if task_raw:
        d        = json.loads(task_raw)
        summary  = d.get("summary", "")
        user     = d.get("user", user)
        start_dt = datetime.fromisoformat(d["start"]) if d.get("start") else None
        await r.delete(f"task_active:{issue_key}")
    else:
        issue, error = await get_issue(issue_key)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return
        summary = issue["fields"]["summary"]

    if action == "done":
        success = await do_transition(issue_key, "완료")
        if not success:
            await interaction.followup.send(f"❌ Done 전환 실패")
            return

    today   = now.astimezone(KST).strftime("%Y-%m-%d")
    log_key = f"work_log:{today}"
    await r.rpush(log_key, json.dumps({
        "issue_key": issue_key,
        "summary":   summary,
        "user":      user,
        "start":     start_dt.isoformat() if start_dt else None,
        "end":       now.isoformat(),
        "action":    action,
    }))
    await r.expire(log_key, 86400 * 7)

    if action == "done":
        msg = (
            f"✅ task `{issue_key}` {summary}\n"
            f"**{user}** | {fmt_dt(start_dt) if start_dt else '?'} → {fmt_dt(now)}"
            f" ({fmt_duration(start_dt, now) if start_dt else ''})\n"
            f"Well done, **{user}**! 🎉"
        )
    else:
        msg = (
            f"🟥 task `{issue_key}` [작업 중지]\n"
            f"**{user}** | {fmt_dt(start_dt) if start_dt else '?'} → {fmt_dt(now)}"
            f" ({fmt_duration(start_dt, now) if start_dt else ''})"
        )

    await interaction.followup.send(msg)


# ── 재시작 확인 버튼 ────────────────────────────────────────────────────────────

class RestartConfirmView(discord.ui.View):
    def __init__(self, issue_key: str, summary: str, assignee: discord.Member):
        super().__init__(timeout=60)
        self.issue_key = issue_key
        self.summary   = summary
        self.assignee  = assignee

    @discord.ui.button(label="y", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        success = await do_transition(self.issue_key, "진행 중")
        if not success:
            await interaction.followup.send(f"❌ In Progress 전환 실패")
            return

        r   = await get_redis()
        now = datetime.now(timezone.utc)
        await r.set(f"task_active:{self.issue_key}", json.dumps({
            "issue_key": self.issue_key,
            "summary":   self.summary,
            "user":      self.assignee.display_name,
            "user_id":   self.assignee.id,
            "start":     now.isoformat(),
        }), ex=86400)

        await interaction.followup.send(
            f"🚩 task `{self.issue_key}` {self.summary}\n"
            f"**{self.assignee.mention}** | {fmt_dt(now)}"
        )
        self.stop()

    @discord.ui.button(label="n", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("취소했어요.", ephemeral=True)
        self.stop()


# ── /jira ──────────────────────────────────────────────────────────────────────

@tree.command(name="jira", description="Jira 정보 조회")
@app_commands.describe(action="today: 오늘 활동 요약")
async def jira_command(interaction: discord.Interaction, action: str):
    if action.lower() == "today":
        await interaction.response.defer()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jql   = f'project = {JIRA_PROJECT_KEY} AND updated >= "{today}" ORDER BY updated DESC'
        data, error = await fetch_issues(jql)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        issues = data.get("issues", [])
        if not issues:
            await interaction.followup.send("오늘 업데이트된 이슈가 없어요.")
            return

        groups: dict[str, list] = {}
        for issue in issues:
            f      = issue.get("fields", {})
            status = f.get("status", {}).get("name", "Unknown")
            groups.setdefault(status, []).append({
                "key":      issue.get("key"),
                "summary":  f.get("summary", "(제목 없음)"),
                "assignee": (f.get("assignee") or {}).get("displayName", "미배정"),
            })

        today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
        embed = discord.Embed(title=f"📋 {today_str} Jira 활동 요약", color=0x4285F4)
        status_icons = {"To Do": "📋", "In Progress": "🔄", "Done": "✅"}

        for status, group_issues in groups.items():
            icon  = status_icons.get(status, "•")
            lines = [f"`{i['key']}` {i['summary']} — {i['assignee']}" for i in group_issues]
            embed.add_field(
                name=f"{icon} {status} ({len(group_issues)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed)
    else:
        await interaction.response.send_message("사용 가능: `/jira today`", ephemeral=True)


tree.add_command(task_group)


@client.event
async def on_ready():
    await tree.sync()
    print(f"Discord bot ready: {client.user}")


async def start_bot():
    if not DISCORD_BOT_TOKEN:
        print("DISCORD_BOT_TOKEN not set, skipping bot startup")
        return
    await client.start(DISCORD_BOT_TOKEN)
