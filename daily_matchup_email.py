import os
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import quote

LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID")
YEAR = os.getenv("ESPN_YEAR", "2026")
TEAM_NAME = os.getenv("ESPN_TEAM_NAME", "Dump")
SEND_TO_EMAIL = os.getenv("SEND_TO_EMAIL")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

ESPN_TEAM_ID_TO_ABBR = {
    1: "BAL", 2: "BOS", 3: "LAA", 4: "CWS", 5: "CLE",
    6: "DET", 7: "KC", 8: "MIL", 9: "MIN", 10: "NYY",
    11: "OAK", 12: "SEA", 13: "TEX", 14: "TOR", 15: "ATL",
    16: "CHC", 17: "CIN", 18: "HOU", 19: "LAD", 20: "WSH",
    21: "NYM", 22: "PHI", 23: "PIT", 24: "STL", 25: "SD",
    26: "SF", 27: "COL", 28: "MIA", 29: "ARI", 30: "TB"
}

MLB_ID_TO_ABBR = {
    110: "BAL", 111: "BOS", 108: "LAA", 145: "CWS", 114: "CLE",
    116: "DET", 118: "KC", 158: "MIL", 142: "MIN", 147: "NYY",
    133: "OAK", 136: "SEA", 140: "TEX", 141: "TOR", 144: "ATL",
    112: "CHC", 113: "CIN", 117: "HOU", 119: "LAD", 120: "WSH",
    121: "NYM", 143: "PHI", 134: "PIT", 138: "STL", 135: "SD",
    137: "SF", 115: "COL", 146: "MIA", 109: "ARI", 139: "TB"
}


def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = SEND_TO_EMAIL

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)


def get_espn_league():
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
    params = {"view": ["mTeam", "mRoster", "mSettings"]}

    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        raise Exception("Could not connect to ESPN league.")

    return response.json()


def get_my_team(league):
    for team in league.get("teams", []):
        if str(team.get("name", "")).lower().strip() == TEAM_NAME.lower().strip():
            return team

    available = [team.get("name", "") for team in league.get("teams", [])]
    raise Exception(f"Could not find ESPN team named {TEAM_NAME}. Available teams: {available}")


def search_mlb_player_id(player_name):
    if not player_name:
        return None

    url = f"https://statsapi.mlb.com/api/v1/people/search?names={quote(player_name)}"
    response = requests.get(url, timeout=30)

    if response.status_code != 200:
        return None

    people = response.json().get("people", [])

    if not people:
        return None

    return people[0].get("id")


def get_full_roster(team):
    players = []

    for entry in team.get("roster", {}).get("entries", []):
        player = entry.get("playerPoolEntry", {}).get("player", {})

        name = player.get("fullName")
        espn_team_id = player.get("proTeamId")
        team_abbr = ESPN_TEAM_ID_TO_ABBR.get(espn_team_id, "UNKNOWN")

        lineup_slot = entry.get("lineupSlotId")
        is_bench = lineup_slot == 20

        mlb_id = search_mlb_player_id(name)

        players.append({
            "name": name,
            "batter_id": mlb_id,
            "team_abbr": team_abbr,
            "is_bench": is_bench
        })

    return players


def get_today_probable_pitchers():
    today = datetime.today().strftime("%Y-%m-%d")

    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": today,
        "hydrate": "probablePitcher"
    }

    response = requests.get(url, params=params, timeout=30)
    if response.status_code != 200:
        raise Exception("Could not pull MLB probable pitchers.")

    data = response.json()
    pitchers_by_team = {}

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            teams = game.get("teams", {})

            away = teams.get("away", {})
            home = teams.get("home", {})

            away_team = away.get("team", {})
            home_team = home.get("team", {})

            away_id = away_team.get("id")
            home_id = home_team.get("id")

            away_abbr = MLB_ID_TO_ABBR.get(away_id)
            home_abbr = MLB_ID_TO_ABBR.get(home_id)

            away_pitcher = away.get("probablePitcher")
            home_pitcher = home.get("probablePitcher")

            if away_abbr and home_pitcher:
                pitchers_by_team[away_abbr] = {
                    "pitcher_name": home_pitcher.get("fullName"),
                    "pitcher_id": home_pitcher.get("id"),
                    "opponent_team": home_team.get("name")
                }

            if home_abbr and away_pitcher:
                pitchers_by_team[home_abbr] = {
                    "pitcher_name": away_pitcher.get("fullName"),
                    "pitcher_id": away_pitcher.get("id"),
                    "opponent_team": away_team.get("name")
                }

    return pitchers_by_team


def get_bvp_stats(batter_id, pitcher_id):
    if not batter_id or not pitcher_id:
        return {
            "summary": "Missing player ID.",
            "ab": 0,
            "hr": 0,
            "k": 0
        }

    url = f"https://statsapi.mlb.com/api/v1/people/{batter_id}"

    params = {
        "hydrate": f"stats(group=[hitting],type=[vsPlayer],opposingPlayerId={pitcher_id},sportId=1)"
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        return {
            "summary": "Could not pull MLB matchup stats.",
            "ab": 0,
            "hr": 0,
            "k": 0
        }

    data = response.json()
    people = data.get("people", [])

    if not people:
        return {
            "summary": "No MLB player found.",
            "ab": 0,
            "hr": 0,
            "k": 0
        }

    stats_blocks = people[0].get("stats", [])

    if not stats_blocks:
        return {
            "summary": "No recorded matchup history.",
            "ab": 0,
            "hr": 0,
            "k": 0
        }

    splits = stats_blocks[0].get("splits", [])

    if not splits:
        return {
            "summary": "No recorded matchup history.",
            "ab": 0,
            "hr": 0,
            "k": 0
        }

    stat = splits[0].get("stat", {})

    ab = int(stat.get("atBats", 0))
    hits = int(stat.get("hits", 0))
    hr = int(stat.get("homeRuns", 0))
    rbi = int(stat.get("rbi", 0))
    avg = stat.get("avg", ".000")
    ops = stat.get("ops", ".000")
    k = int(stat.get("strikeOuts", 0))
    bb = int(stat.get("baseOnBalls", 0))

    summary = f"{hits} H, {hr} HR, {rbi} RBI, {ab} AB, AVG {avg}, OPS {ops}, {bb} BB, {k} K"

    return {
        "summary": summary,
        "ab": ab,
        "hr": hr,
        "k": k
    }


def build_email(roster, pitchers_by_team):
    today = datetime.today().strftime("%B %d, %Y")

    lines = [
        "Good morning Cam,",
        "",
        f"Here is your full-roster ESPN fantasy baseball matchup report for {today}.",
        "",
        "This includes both active and bench players from Dump.",
        ""
    ]

    for player in roster:
        name = player["name"]
        team_abbr = player["team_abbr"]
        bench_note = "Bench" if player["is_bench"] else "Active"

        matchup = pitchers_by_team.get(team_abbr)

        if not matchup:
            lines.append(f"{name} ({bench_note}) - {team_abbr}")
            lines.append("No probable pitcher found today, or player's team may be off.")
            lines.append("")
            continue

        pitcher_name = matchup["pitcher_name"]
        pitcher_id = matchup["pitcher_id"]
        opponent_team = matchup["opponent_team"]

        history = get_bvp_stats(player["batter_id"], pitcher_id)

        lines.append(f"{name} ({bench_note}) vs {pitcher_name} ({opponent_team})")
        lines.append(f"Career matchup: {history['summary']}")

        if history["ab"] >= 5 and history["hr"] > 0:
            lines.append("Quick read: Strong power history in this matchup.")
        elif history["ab"] >= 5 and history["k"] >= 3:
            lines.append("Quick read: Some strikeout risk here.")
        elif history["ab"] == 0:
            lines.append("Quick read: No direct matchup history.")
        else:
            lines.append("Quick read: Small sample. Use as light context.")

        lines.append("")

    return "\n".join(lines)


def main():
    league = get_espn_league()
    team = get_my_team(league)
    roster = get_full_roster(team)
    pitchers_by_team = get_today_probable_pitchers()

    body = build_email(roster, pitchers_by_team)

    subject = f"Daily Fantasy Baseball Matchup Report - {datetime.today().strftime('%b %d')}"

    send_email(subject, body)

    print("Email sent successfully.")


if __name__ == "__main__":
    main()
