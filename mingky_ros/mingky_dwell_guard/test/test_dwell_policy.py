import pytest

from mingky_dwell_guard.dwell_policy import DwellPolicy, DwellTimer


def timer(timeout=10.0):
    return DwellTimer(DwellPolicy(timeout_sec=timeout))


def test_대기가_아니면_아무_일도_없다():
    t = timer()
    assert t.update('normal', 0.0) is False
    assert t.update('slow', 100.0) is False
    assert t.update(None, 200.0) is False


def test_시간이_차면_한_번_알린다():
    t = timer(10.0)
    assert t.update('waiting', 0.0) is False
    assert t.update('waiting', 9.9) is False
    assert t.update('waiting', 10.0) is True


def test_시간이_찬_뒤에도_한_번만_알린다():
    """상태는 계속 waiting 이다. 매번 알리면 취소 요청이 초당 수십 번 나간다."""
    t = timer(10.0)
    t.update('waiting', 0.0)
    assert t.update('waiting', 10.0) is True
    assert t.update('waiting', 10.5) is False
    assert t.update('waiting', 60.0) is False


def test_환자가_돌아오면_처음부터_다시_센다():
    """짧게 여러 번 놓친 것을 합산하면 안 된다.

    합산하면 환자가 잘 따라오는데도 순간적으로 몇 번 놓쳤다는 이유만으로
    안내를 접게 된다.
    """
    t = timer(10.0)
    t.update('waiting', 0.0)
    t.update('waiting', 9.0)
    t.update('normal', 9.5)          # 환자가 돌아왔다
    assert t.update('waiting', 10.0) is False
    assert t.update('waiting', 19.0) is False
    assert t.update('waiting', 20.0) is True


def test_돌아왔다_다시_놓치면_다시_알릴_수_있다():
    t = timer(10.0)
    t.update('waiting', 0.0)
    assert t.update('waiting', 10.0) is True
    t.update('normal', 11.0)
    t.update('waiting', 12.0)
    assert t.update('waiting', 22.0) is True


def test_시계가_뒤로_가도_일찍_포기하지_않는다():
    """시각 보정 등으로 시계가 뒤로 갈 수 있다.

    그때 기다린 시간을 그대로 믿으면 음수가 되거나, 반대로 다음 tick 에서
    갑자기 오래 기다린 것으로 계산돼 즉시 포기할 수 있다.
    """
    t = timer(10.0)
    t.update('waiting', 100.0)
    assert t.update('waiting', 50.0) is False   # 시계가 뒤로 갔다
    assert t.update('waiting', 59.9) is False
    assert t.update('waiting', 60.0) is True    # 되감긴 시점부터 다시 센다


def test_남은_시간과_지난_시간():
    t = timer(10.0)
    assert t.remaining(0.0) == 0.0
    t.update('waiting', 0.0)
    assert t.elapsed(4.0) == pytest.approx(4.0)
    assert t.remaining(4.0) == pytest.approx(6.0)
    t.update('normal', 5.0)
    assert t.remaining(5.0) == 0.0


def test_기다린_시간은_0_밑으로_안_내려간다():
    t = timer(10.0)
    t.update('waiting', 10.0)
    assert t.elapsed(5.0) == 0.0


@pytest.mark.parametrize('bad', [0.0, -1.0])
def test_잘못된_시간_설정은_거부한다(bad):
    with pytest.raises(ValueError):
        DwellPolicy(timeout_sec=bad)
