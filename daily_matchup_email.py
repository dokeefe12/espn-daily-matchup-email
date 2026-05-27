import os
import smtplib
import requests
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

    params = {
        "view": ["mTeam", "mRoster", "mSettings"]
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code != 200:
        print(response.text[:1000])
        raise Exception("Could not connect to ESPN league.")

    return response.json()


def get_my_team(league):
    for team in league.get("teams", []):
        if str(team.get("name", "")).lower().strip() == TEAM_NAME.lower().strip():
            return team

    available = [team.get("name", "") for team in league.get("teams", [])]
    raise Exception(f"Could not find ESPN team named {TEAM_NAME}. Available teams: {available}")


def get_full_roster(team):
    players = []

    for entry in team.get("roster", {}).get("entries", []):
        player = entry.get("playerPoolEntry", {}).get("player", {})

        espn_team_id = player.get("proTeamId")
        team_abbr = ESPN_TEAM_ID_TO_ABBR.get(espn_team_id, "UNKNOWN")

        lineup_slot = entry.get("lineupSlotId")
        is_bench = lineup_slot == 20

        players.append({
            "name": player.get("fullName"),
            "batter_id": player.get("id"),
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


def get_statcast_data():
    end_date = datetime.today().strftime("%Y-%m-%d")
    return statcast(start_dt=SEASON_START_DATE, end_dt=end_date)


def get_matchup_history(statcast_data, batter_id, pitcher_id):
    matchup = statcast_data[
        (statcast_data["batter"].astype(str) == str(batter_id)) &
        (statcast_data["pitcher"].astype(str) == str(pitcher_id))
    ]

    if matchup.empty:
        return "No recorded Statcast matchup history.", 0, 0, 0, 0

    events = matchup.dropna(subset=["events"])

    hits = events["events"].isin(["single", "double", "triple", "home_run"]).sum()
    hr = (events["events"] == "home_run").sum()
    k = (events["events"] == "strikeout").sum()
    bb = events["events"].isin(["walk", "intent_walk"]).sum()

    non_ab = ["walk", "intent_walk", "hit_by_pitch", "sac_fly", "sac_bunt", "catcher_interf"]
    ab = len(events[~events["events"].isin(non_ab)])

    avg = hits / ab if ab else 0

    summary = f"{hits}-{ab}, {hr} HR, {bb} BB, {k} K, AVG {avg:.3f}"
    return summary, ab, hr, k, bb


def build_email(roster, pitchers_by_team, statcast_data):
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

        summary, ab, hr, k, bb = get_matchup_history(
            statcast_data,
            player["batter_id"],
            pitcher_id
        )

        lines.append(f"{name} ({bench_note}) vs {pitcher_name} ({opponent_team})")
        lines.append(f"Career matchup: {summary}")

        if ab >= 5 and hr > 0:
            lines.append("Quick read: Strong power history in this matchup.")
        elif ab >= 5 and k >= 3:
            lines.append("Quick read: Some strikeout risk here.")
        elif ab == 0:
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

    print("Pitchers by team:")
    print(pitchers_by_team)

    statcast_data = get_statcast_data()

    body = build_email(roster, pitchers_by_team, statcast_data)

    subject = f"Daily Fantasy Baseball Matchup Report - {datetime.today().strftime('%b %d')}"

    send_email(subject, body)

    print("Email sent successfully.")


if __name__ == "__main__":
    main()
