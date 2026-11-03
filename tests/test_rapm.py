from app.models.rapm import _possessions, build_stints

HOME, AWAY = 10, 20


def _play(seq, play_type, team_id=None, player_in=None, player_out=None,
          home_score=0, away_score=0):
    return {
        "seq": seq,
        "period": 1,
        "clock": "12:00",
        "play_type": play_type,
        "team_id": team_id,
        "player_in": player_in,
        "player_out": player_out,
        "home_score": home_score,
        "away_score": away_score,
    }


def _starters():
    return {HOME: [1, 2, 3, 4, 5], AWAY: [6, 7, 8, 9, 10]}


def test_stints_reconstruct_lineups_and_margins():
    plays = [
        _play(1, "Jumpball", home_score=0, away_score=0),
        # home scores 4 with the starters on the floor
        _play(2, "Layup Shot", HOME, home_score=2, away_score=0),
        _play(3, "Jump Shot", HOME, home_score=4, away_score=0),
        # sub: home 5 out, 11 in (boundary diff must seed the next stint)
        _play(4, "Substitution", HOME, player_in=11, player_out=5, home_score=4, away_score=0),
        # away scores 3, then home scores 2 with 11 on the floor
        _play(5, "Jump Shot", AWAY, home_score=4, away_score=3),
        _play(6, "Dunk Shot", HOME, home_score=6, away_score=3),
    ]
    stints = build_stints(_starters(), plays, "g1", HOME, AWAY)
    assert len(stints) == 2
    first, second = stints
    assert first["home_players"] == [1, 2, 3, 4, 5]
    assert first["margin"] == 4
    assert second["home_players"] == [1, 2, 3, 4, 11]
    assert second["margin"] == -1  # away +3, home +2
    assert first["margin"] + second["margin"] == 3  # reconciles with final diff (6-3)


def test_stints_skip_games_without_five_starters():
    starters = {HOME: [1, 2, 3, 4], AWAY: [6, 7, 8, 9, 10]}
    plays = [_play(1, "Jumpball")]
    assert build_stints(starters, plays, "g1", HOME, AWAY) == []


def test_possessions_heuristic():
    plays = [
        _play(1, "Jump Shot", HOME),          # FGA
        _play(2, "Offensive Rebound", HOME),  # OREB (cancels the FGA)
        _play(3, "Defensive Rebound", AWAY),
        _play(4, "Bad Pass\nTurnover", HOME),  # TOV
        _play(5, "Free Throw - 1 of 2", HOME),  # 2 FTA
        _play(6, "Free Throw - 2 of 2", HOME),
        _play(7, "Jumpball", HOME),           # not a shot
        _play(8, "End Period", None),
    ]
    # 1 FGA + 0.44*2 FTA - 1 OREB + 1 TOV = 1 + 0.88 - 1 + 1 = 1.88 -> 2
    assert _possessions(plays) == 2
