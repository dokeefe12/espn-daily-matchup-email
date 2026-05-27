import os
import smtplib
import requests
import pandas as pd
from datetime import datetime
from email.mime.text import MIMEText
from pybaseball import statcast

LEAGUE_ID = os.getenv("ESPN_LEAGUE_ID")
YEAR = os.getenv("ESPN_YEAR", "2026")
TEAM_NAME = os.getenv("ESPN_TEAM_NAME", "Dump")
SEND_TO_EMAIL = os.getenv("SEND_TO_EMAIL")

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

SEASON_START_DATE = f"{YEAR}-03-01"


def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_USER
    msg["To"] = SEND_TO_EMAIL

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)


def get_espn_league():
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/"
        f"seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
    )

    params = {
        "view": ["mTeam", "mRoster", "mSettings"]
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        raise Exception("Could not connect to ESPN league.")

    return response.json()


def get_my_team(league):
    for team in league.get("teams", []):
        if team.get("name", "").lower() == TEAM_NAME.lower():
            return team

    raise Exception(f"Could not find ESPN team named {TEAM_NAME}")


def get_full_roster(team):
    players = []

    for entry in team.get("roster", {}).get("entries", []):
        player = entry.get("playerPoolEntry", {}).get("player", {})

        name = player.get("fullName")
        mlb_id = player.get("id")
        pro_team_id = player.get("proTeamId")

        lineup_slot = entry.get("lineupSlotId")
        is_bench = lineup_slot == 20

        players.append({
            "name": name,
            "mlb_id": mlb_id,
            "pro_team_id": pro_team_id,
            "lineup_slot": lineup_slot,
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

            away_team_id = away.get("team", {}).get("id")
            home_team_id = home.get("team", {}).get("id")

            away_pitcher = away.get("probablePitcher", {})
            home_pitcher = home.get("probablePitcher", {})

            if away_team_id and home_pitcher:
                pitchers_by_team[away_team_id] = {
                    "opponent_pitcher_name": home_pitcher.get("fullName"),
                    "opponent_pitcher_id": home_pitcher.get("id"),
                    "opponent_team": home.get("team", {}).get("name")
                }

            if home_team_id and away_pitcher:
                pitchers_by_team[home_team_id] = {
                    "opponent_pitcher_name": away_pitcher.get("fullName"),
                    "opponent_pitcher_id": away_pitcher.get("id"),
                    "opponent_team": away.get("team", {}).get("name")
                }

    return pitchers_by_team


def get_batter_vs_pitcher_history(batter_id, pitcher_id):
    end_date = datetime.today().strftime("%Y-%m-%d")

    data = statcast(start_dt=SEASON_START_DATE, end_dt=end_date)

    matchup = data[
        (data["batter"].astype(str) == str(batter_id)) &
        (data["pitcher"].astype(str) == str(pitcher_id))
    ]

    if matchup.empty:
        return {
            "pa": 0,
            "ab": 0,
            "hits": 0,
            "hr": 0,
            "k": 0,
            "bb": 0,
            "summary": "No recorded Statcast matchup history."
        }

    events = matchup.dropna(subset=["events"])

    hits = events["events"].isin(["single", "double", "triple", "home_run"]).sum()
    hr = (events["events"] == "home_run").sum()
    k = (events["events"] == "strikeout").sum()
    bb = events["events"].isin(["walk", "intent_walk"]).sum()

    non_ab_events = [
        "walk",
        "intent_walk",
        "hit_by_pitch",
        "sac_fly",
        "sac_bunt",
        "catcher_interf"
    ]

    ab = len(events[~events["events"].isin(non_ab_events)])
    pa = len(events)

    avg = hits / ab if ab else 0

    return {
        "pa": pa,
        "ab": ab,
        "hits": hits,
        "hr": hr,
        "k": k,
        "bb": bb,
        "avg": avg,
        "summary": f"{hits}-{ab}, {hr} HR, {bb} BB, {k} K"
    }


def build_email(roster, pitchers_by_team):
    today = datetime.today().strftime("%B %d, %Y")

    lines = [
        f"Good morning Cam,",
        "",
        f"Here is your full-roster ESPN fantasy baseball matchup report for {today}.",
        "",
        "This includes starters and bench players from Dump.",
        ""
    ]

    for player in roster:
        name = player["name"]
        batter_id = player["mlb_id"]
        team_id = player["pro_team_id"]
        bench_note = "Bench" if player["is_bench"] else "Active"

        matchup = pitchers_by_team.get(team_id)

        if not matchup:
            lines.append(f"{name} ({bench_note})")
            lines.append("No probable pitcher found for today, or player’s team may be off.")
            lines.append("")
            continue

        pitcher_name = matchup["opponent_pitcher_name"]
        pitcher_id = matchup["opponent_pitcher_id"]

        history = get_batter_vs_pitcher_history(batter_id, pitcher_id)

        lines.append(f"{name} ({bench_note}) vs {pitcher_name}")
        lines.append(f"Career matchup: {history['summary']}")

        if history.get("ab", 0) >= 5 and history.get("hr", 0) > 0:
            lines.append("Quick read: Strong power history in this matchup.")
        elif history.get("ab", 0) >= 5 and history.get("k", 0) >= 3:
            lines.append("Quick read: Some swing-and-miss risk here.")
        elif history.get("pa", 0) == 0:
            lines.append("Quick read: No direct matchup history, so use recent form and pitcher quality.")
        else:
            lines.append("Quick read: Small sample. Treat this as light context, not a lock.")

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
