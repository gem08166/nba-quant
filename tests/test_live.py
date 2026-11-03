from app.jobs.live import fraction_played, game_seconds, live_projection

NBAQ = 4 * 720  # 2880s regulation


def test_game_seconds_clock_math():
    # tip-off
    assert game_seconds(1, 720) == 0
    # end of Q1
    assert game_seconds(1, 0) == 720
    # halftime
    assert game_seconds(2, 360) == 1080
    # end of regulation
    assert game_seconds(4, 0) == NBAQ
    # mid-OT1
    assert game_seconds(5, 150) == NBAQ + 150


def test_fraction_played_bounds():
    assert fraction_played(1, 720) == 0.0
    assert fraction_played(2, 0) == 0.5
    assert fraction_played(4, 0) == 1.0
    # OT does not exceed 1.0 (scoring projection capped)
    assert fraction_played(5, 0) == 1.0


def test_live_projection_at_start_matches_pregame():
    proj = live_projection(
        pregame_margin=-3.0, pregame_total=225.0,
        sigma_margin=10.5, sigma_total=13.5,
        period=1, clock_seconds=720,
        current_margin=0, current_total=0,
    )
    assert abs(proj["proj_margin"] - (-3.0)) < 1e-9
    assert abs(proj["proj_total"] - 225.0) < 1e-9
    assert abs(proj["sigma_margin"] - 10.5) < 1e-9


def test_live_projection_at_halftime():
    # home leads by 10 at half; pregame said home -4
    proj = live_projection(
        pregame_margin=4.0, pregame_total=224.0,
        sigma_margin=10.5, sigma_total=13.5,
        period=3, clock_seconds=720,  # start of Q3 = exactly half played
        current_margin=10, current_total=118,
    )
    assert abs(proj["remaining_factor"] - 0.5) < 1e-9
    assert abs(proj["proj_margin"] - (10 + 4 * 0.5)) < 1e-9
    assert abs(proj["proj_total"] - (118 + 224 * 0.5)) < 1e-9
    # sigma shrank by sqrt(remaining time)
    assert proj["sigma_margin"] < 10.5
    assert proj["home_win_prob"] > 0.5  # leading + pregame favored


def test_live_projection_final_buzzer():
    proj = live_projection(
        pregame_margin=0.0, pregame_total=220.0,
        sigma_margin=10.5, sigma_total=13.5,
        period=4, clock_seconds=0,
        current_margin=7, current_total=215,
    )
    assert proj["proj_margin"] == 7
    assert proj["proj_total"] == 215
    assert proj["home_win_prob"] > 0.99
