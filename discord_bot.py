import os
import discord
from discord import app_commands
from datetime import datetime, timezone
import httpx
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "MADI")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def get_jira_auth():
    import base64
    credentials = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}


async def fetch_today_issues():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jql = f'project = {JIRA_PROJECT_KEY} AND updated >= "{today}" ORDER BY updated DESC'

    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    print(f"Requesting: {url}")
    print(f"Auth email: {JIRA_EMAIL}")
    print(f"JQL: {jql}")

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            url,
            headers=get_jira_auth(),
            params={
                "jql": jql,
                "maxResults": 20,
                "fields": "summary,status,assignee,priority,issuetype,updated"
            },
            timeout=10,
        )
        print(f"Response status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Response body: {resp.text}")
            return None, f"Jira API 오류: {resp.status_code}"
        return resp.json(), None


@tree.command(name="jira", description="Jira 정보를 조회합니다")
@app_commands.describe(action="today: 오늘 활동 요약")
async def jira_command(interaction: discord.Interaction, action: str):
    if action.lower() == "today":
        await interaction.response.defer()

        data, error = await fetch_today_issues()
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
                "priority": f.get("priority", {}).get("name", "?"),
            })

        today_str = datetime.now(timezone.utc).strftime("%Y년 %m월 %d일")
        embed = discord.Embed(
            title=f"📋 {today_str} Jira 활동 요약",
            color=0x7289DA,
        )

        status_icons = {
            "To Do": "📋",
            "In Progress": "🔄",
            "Done": "✅",
            "In Review": "👀",
        }

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

        embed.set_footer(text=f"총 {len(issues)}개 이슈 · {JIRA_PROJECT_KEY} 프로젝트")
        await interaction.followup.send(embed=embed)
    else:
        await interaction.response.send_message(
            "사용 가능한 명령어: `/jira today`", ephemeral=True
        )


@client.event
async def on_ready():
    await tree.sync()
    print(f"Discord bot ready: {client.user}")


async def start_bot():
    if not DISCORD_BOT_TOKEN:
        print("DISCORD_BOT_TOKEN not set, skipping bot startup")
        return
    await client.start(DISCORD_BOT_TOKEN)
