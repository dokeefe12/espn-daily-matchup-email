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
        print(response.text[:1000])
        raise Exception("Could not connect to ESPN league.")

    return response.json()


def clean_name(name):
    return str(name).lower().strip()


def get_my_team(league):
    target = clean_name(TEAM_NAME)

    for team in league.get("teams", []):
        espn_name = team.get("name", "")
        if clean_name(espn_name) == target:
            return team

    available = [team.get("name", "") for team in league.get("teams", [])]
    raise Exception(f"Could not find ESPN team named {TEAM_NAME}. Available teams: {available}")


def get_full_roster(team):
    players = []

    for entry in team.get("roster", {}).get("entries", []):
        player = entry.get("playerPoolEntry", {}).get("player", {})

        name = player.get("fullName")
        batter_id = player.get("id")
        espn_team_id = player.get("proTeamId")
        mlb_team_abbr = ESPN_TEAM_ID_TO_ABBR.get(espn_team_id, "UNKNOWN")

        lineup_slot = entry.get("lineupSlotId")
        is_bench = lineup_slot == 20

        players.append({
            "name": name,
            "batter_id": batter_id,
            "espn_team_id": espn_team_id,
            "mlb_team_abbr": mlb_team_abbr,
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

    pitchers_by_team_abbr = {}

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            teams = game.get("teams", {})

            away = teams.get("away", {})
            home = teams.get("home", {})

            away_team = away.get("team", {})
            home_team = home.get("team", {})

            away_abbr = away_team.get("abbreviation")
            home_abbr = home_team.get("abbreviation")

            away_pitcher = away.get("probablePitcher", {})
            home_pitcher = home.get("probablePitcher", {})

            if away_abbr and home_pitcher:
                pitchers_by_team_abbr[away_abbr] = {
                    "opponent_pitcher_name": home_pitcher.get("fullName"),
                    "opponent_pitcher_id": home_pitcher.get("id"),
                    "opponent_team": home_team.get("name")
                }

            if home_abbr and away_pitcher:
                pitchers_by_team_abbr[home_abbr] = {
                    "opponent_pitcher_name": away_pitcher.get("fullName"),
                    "opponent_pitcher_id": away_pitcher.get("id"),
                    "opponent_team": away_team.get("name")
                }

    return pitchers_by_team_abbr


def get_all_statcast_data():
    end_date = datetime.today().strftime("%Y-%m-%d")
    print(f"Pulling Statcast data from {SEASON_START_DATE} to {end_date}")
    return statcast(start_dt=SEASON_START_DATE, end_dt=end_date)


def get_batter_vs_pitcher_history(statcast_data, batter_id, pitcher_id):
    if not batter_id or not pitcher_id:
        return {
            "pa": 0,
            "ab": 0,
            "hits": 0,
            "hr": 0,
            "k": 0,
            "bb": 0,
            "avg": 0,
            "summary": "Missing batter or pitcher ID."
        }

    matchup = statcast_data[
        (statcast_data["batter"].astype(str) == str(batter_id)) &
        (statcast_data["pitcher"].astype(str) == str(pitcher_id))
    ]

    if matchup.empty:
        return {
            "pa": 0,
            "ab": 0,
            "hits": 0,
            "hr": 0,
            "k": 0,
            "bb": 0,
            "avg": 0,
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
        "summary": f"{hits}-{ab}, {hr} HR, {bb} BB, {k} K, AVG {avg:.3f}"
    }


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
        batter_id = player["batter_id"]
        team_abbr = player["mlb_team_abbr"]
        bench_note = "Bench" if player["is_bench"] else "Active"

        matchup = pitchers_by_team.get(team_abbr)

        if not matchup:
            lines.append(f"{name} ({bench_note}) - {team_abbr}")
            lines.append("No MLB probable pitcher found today, or player's team may be off.")
            lines.append("")
            continue

        pitcher_name = matchup["opponent_pitcher_name"]
        pitcher_id = matchup["opponent_pitcher_id"]
        opponent_team = matchup["opponent_team"]

        history = get_batter_vs_pitcher_history(
            statcast_data,
            batter_id,
            pitcher_id
        )

        lines.append(f"{name} ({bench_note}) vs {pitcher_name} ({opponent_team})")
        lines.append(f"Career matchup: {history['summary']}")

        if history.get("ab", 0) >= 5 and history.get("hr", 0) > 0:
            lines.append("Quick read: Strong power history in this matchup.")
        elif history.get("ab", 0) >= 5 and history.get("k", 0) >= 3:
            lines.append("Quick read: Some swing-and-miss risk here.")
        elif history.get("pa", 0) == 0:
            lines.append("Quick read: No direct matchup history. Use recent form and lineup spot as extra context.")
        else:
            lines.append("Quick read: Small sample. Treat this as light context, not a lock.")

        lines.append("")

    return "\n".join(lines)


def main():
    league = get_espn_league()
    team = get_my_team(league)
    roster = get_full_roster(team)

    pitchers_by_team = get_today_probable_pitchers()

    print("Probable pitchers by team:")
    print(pitchers_by_team)

    statcast_data = get_all_statcast_data()

    body = build_email(roster, pitchers_by_team, statcast_data)

    subject = f"Daily Fantasy Baseball Matchup Report - {datetime.today().strftime('%b %d')}"

    send_email(subject, body)

    print("Email sent successfully.")


if __name__ == "__main__":
    main()
