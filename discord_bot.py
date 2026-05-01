import os
import discord
from discord import app_commands
from datetime import datetime, timezone
import httpx
import redis.asyncio as aioredis
import json
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = "MADI"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

redis_client = None


async def get_redis():
    global redis_client
    if redis_client is None:
        redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    return redis_client


def get_jira_auth():
    import base64
    credentials = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def fmt_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total = int(delta.total_seconds())
    h, m = divmod(total // 60, 60)
    if h > 0:
        return f"{h}시간 {m}분"
    return f"{m}분"


async def fetch_issues(jql: str, fields: str = "summary,status,assignee,priority,issuetype"):
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
        # 부분 매칭
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


# ── /jira ──────────────────────────────────────────────────────────────────────

@tree.command(name="jira", description="Jira 정보를 조회합니다")
@app_commands.describe(action="today: 오늘 활동 요약 / work: 오늘 작업 기록")
async def jira_command(interaction: discord.Interaction, action: str):

    if action.lower() == "today":
        await interaction.response.defer()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jql = f'project = {JIRA_PROJECT_KEY} AND updated >= "{today}" ORDER BY updated DESC'
        data, error = await fetch_issues(jql)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return

        issues = data.get("issues", [])
        if not issues:
            await interaction.followup.send("오늘 업데이트된 이슈가 없어요.")
            return

        groups = {}
        for issue in issues:
            f = issue.get("fields", {})
            status = f.get("status", {}).get("name", "Unknown")
            if status not in groups:
                groups[status] = []
            groups[status].append({
                "key": issue.get("key"),
                "summary": f.get("summary", "(제목 없음)"),
                "assignee": (f.get("assignee") or {}).get("displayName", "미배정"),
            })

        today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
        embed = discord.Embed(title=f"📋 {today_str} Jira 활동 요약", color=0x7289DA)
        status_icons = {"To Do": "📋", "In Progress": "🔄", "Done": "✅", "In Review": "👀"}

        for status, group_issues in groups.items():
            icon = status_icons.get(status, "•")
            lines = []
            for i in group_issues:
                url = f"{JIRA_BASE_URL}/browse/{i['key']}"
                lines.append(f"[{i['key']}]({url}) {i['summary']} — {i['assignee']}")
            embed.add_field(
                name=f"{icon} {status} ({len(group_issues)})",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        embed.set_footer(text=f"총 {len(issues)}개 이슈 · {JIRA_PROJECT_KEY}")
        await interaction.followup.send(embed=embed)

    elif action.lower() == "work":
        await interaction.response.defer()
        r = await get_redis()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"work_log:{today}"

        logs_raw = await r.lrange(key, 0, -1)
        if not logs_raw:
            await interaction.followup.send("오늘 기록된 작업이 없어요.")
            return

        logs = [json.loads(l) for l in logs_raw]

        embed = discord.Embed(
            title=f"🗂️ {today} 작업 기록",
            color=0x7289DA,
        )

        # 유저별 그룹핑
        user_logs = {}
        for log in logs:
            user = log.get("user", "?")
            if user not in user_logs:
                user_logs[user] = []
            user_logs[user].append(log)

        for user, ulogs in user_logs.items():
            lines = []
            for log in ulogs:
                issue_key = log.get("issue_key")
                summary = log.get("summary", "")
                start = log.get("start")
                end = log.get("end")
                action_type = log.get("action", "done")

                if start and end:
                    start_dt = datetime.fromisoformat(start)
                    end_dt = datetime.fromisoformat(end)
                    duration = fmt_duration(start_dt, end_dt)
                    icon = "✅" if action_type == "done" else "⏹️"
                    lines.append(f"{icon} [{issue_key}]({JIRA_BASE_URL}/browse/{issue_key}) {summary}\n　{fmt_time(start_dt)} → {fmt_time(end_dt)} ({duration})")
                elif start:
                    start_dt = datetime.fromisoformat(start)
                    lines.append(f"🔄 [{issue_key}]({JIRA_BASE_URL}/browse/{issue_key}) {summary}\n　{fmt_time(start_dt)} 시작 (진행중)")

            embed.add_field(
                name=f"👤 {user}",
                value="\n".join(lines)[:1024] if lines else "(없음)",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    else:
        await interaction.response.send_message(
            "사용 가능한 명령어: `/jira today` / `/jira work`", ephemeral=True
        )


# ── /task ──────────────────────────────────────────────────────────────────────

task_group = app_commands.Group(name="task", description="Jira 태스크 관리")


@task_group.command(name="list", description="전체 이슈 목록")
async def task_list(interaction: discord.Interaction):
    await interaction.response.defer()
    jql = f'project = {JIRA_PROJECT_KEY} ORDER BY status ASC, updated DESC'
    data, error = await fetch_issues(jql)
    if error:
        await interaction.followup.send(f"❌ {error}")
        return

    issues = data.get("issues", [])
    if not issues:
        await interaction.followup.send("이슈가 없어요.")
        return

    groups = {}
    for issue in issues:
        f = issue.get("fields", {})
        status = f.get("status", {}).get("name", "Unknown")
        if status not in groups:
            groups[status] = []
        groups[status].append({
            "key": issue.get("key"),
            "summary": f.get("summary", "(제목 없음)"),
            "assignee": (f.get("assignee") or {}).get("displayName", "미배정"),
        })

    embed = discord.Embed(title=f"📋 {JIRA_PROJECT_KEY} 이슈 목록", color=0x7289DA)
    status_icons = {"To Do": "📋", "In Progress": "🔄", "Done": "✅", "In Review": "👀"}

    for status, group_issues in groups.items():
        icon = status_icons.get(status, "•")
        lines = []
        for i in group_issues:
            url = f"{JIRA_BASE_URL}/browse/{i['key']}"
            lines.append(f"`{i['key']}` [{i['summary']}]({url}) — {i['assignee']}")
        embed.add_field(
            name=f"{icon} {status} ({len(group_issues)})",
            value="\n".join(lines)[:1024],
            inline=False,
        )

    embed.set_footer(text=f"총 {len(issues)}개 이슈")
    await interaction.followup.send(embed=embed)


@task_group.command(name="start", description="태스크 시작 (In Progress로 변경)")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)")
async def task_start(interaction: discord.Interaction, issue_key: str):
    await interaction.response.defer()

    issue_key = issue_key.upper()
    issue, error = await get_issue(issue_key)
    if error:
        await interaction.followup.send(f"❌ {error}")
        return

    summary = issue["fields"]["summary"]
    current_status = issue["fields"]["status"]["name"]

    # In Progress로 전환
    if current_status != "In Progress":
        success = await do_transition(issue_key, "In Progress")
        if not success:
            await interaction.followup.send(f"❌ 상태 변경 실패. 가능한 전환이 없을 수 있어요.")
            return

    # Redis에 시작 시간 기록
    r = await get_redis()
    now = datetime.now(timezone.utc)
    task_key = f"task_active:{issue_key}"
    await r.set(task_key, json.dumps({
        "issue_key": issue_key,
        "summary": summary,
        "user": interaction.user.display_name,
        "start": now.isoformat(),
    }), ex=86400)  # 24시간 TTL

    user = interaction.user.display_name
    embed = discord.Embed(
        title=f"🔄 작업 시작",
        description=f"**[{issue_key}]({JIRA_BASE_URL}/browse/{issue_key}) {summary}**",
        color=0xF39C12,
    )
    embed.add_field(name="시작 시간", value=fmt_time(now), inline=True)
    embed.add_field(name="담당자", value=user, inline=True)
    embed.set_footer(text=f"화이팅! 🔥")
    await interaction.followup.send(embed=embed)


async def _finish_task(interaction: discord.Interaction, issue_key: str, action: str):
    """stop/done 공통 처리"""
    await interaction.response.defer()

    issue_key = issue_key.upper()
    r = await get_redis()
    task_key = f"task_active:{issue_key}"
    task_raw = await r.get(task_key)

    now = datetime.now(timezone.utc)
    user = interaction.user.display_name

    summary = ""
    start_dt = None

    if task_raw:
        task_data = json.loads(task_raw)
        summary = task_data.get("summary", "")
        start_str = task_data.get("start")
        if start_str:
            start_dt = datetime.fromisoformat(start_str)
        await r.delete(task_key)
    else:
        issue, error = await get_issue(issue_key)
        if error:
            await interaction.followup.send(f"❌ {error}")
            return
        summary = issue["fields"]["summary"]

    # Done이면 상태 변경
    if action == "done":
        success = await do_transition(issue_key, "Done")
        if not success:
            await interaction.followup.send(f"❌ Done 전환 실패.")
            return

    # 오늘 작업 로그에 저장
    today = now.strftime("%Y-%m-%d")
    log_key = f"work_log:{today}"
    log_entry = {
        "issue_key": issue_key,
        "summary": summary,
        "user": user,
        "start": start_dt.isoformat() if start_dt else None,
        "end": now.isoformat(),
        "action": action,
    }
    await r.rpush(log_key, json.dumps(log_entry))
    await r.expire(log_key, 86400 * 7)  # 7일 보관

    # 메시지
    if start_dt:
        duration = fmt_duration(start_dt, now)
        desc = f"**Task** `{issue_key}` {summary}\n**from** {fmt_time(start_dt)} **to** {fmt_time(now)} ({duration})"
    else:
        desc = f"**Task** `{issue_key}` {summary}\n**완료** {fmt_time(now)}"

    icon = "✅" if action == "done" else "⏹️"
    color = 0x1ABC9C if action == "done" else 0x95A5A6

    embed = discord.Embed(
        title=f"{icon} {user} finished!",
        description=desc,
        color=color,
    )
    embed.set_footer(text="Well done! Keep it up! 💪")
    await interaction.followup.send(embed=embed)


@task_group.command(name="stop", description="태스크 일시 중단 (상태 유지, 시간 기록)")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)")
async def task_stop(interaction: discord.Interaction, issue_key: str):
    await _finish_task(interaction, issue_key, "stop")


@task_group.command(name="done", description="태스크 완료 (Done으로 변경)")
@app_commands.describe(issue_key="이슈 키 (예: MADI-1)")
async def task_done(interaction: discord.Interaction, issue_key: str):
    await _finish_task(interaction, issue_key, "done")


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
