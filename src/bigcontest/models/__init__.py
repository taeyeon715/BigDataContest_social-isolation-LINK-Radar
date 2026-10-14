"""기대 활동 모델 (0a/0b 기준선, STLForecast, LightGBM)과 평가 도구.

예측(미래 기대 활동)과 관측 후 이탈 판정은 쓸 수 있는 정보가 다르다.
예측 모델은 기준일까지의 결제와 미리 알려진 달력만 쓴다. 날씨·같은 미래 날짜의 다른 코호트 결제는 쓰지 않는다.

STLModel(statsmodels)·LGBMModel 은 처음 꺼낼 때 불러온다 (기준선만 쓰는 정기 감시가 statsmodels 를 부르지 않게).
`from bigcontest.models import STLModel` 은 그대로 동작한다.
"""

from importlib import import_module

from bigcontest.models.baseline import Forecaster, Request, SameWeekdayMean, SeasonalNaive, validate_panel

_LAZY = {"STLModel": "bigcontest.models.stl", "LGBMModel": "bigcontest.models.lgbm"}

__all__ = ["Forecaster", "Request", "SameWeekdayMean", "SeasonalNaive", "STLModel", "LGBMModel", "validate_panel"]


def __getattr__(name: str):
    if name in _LAZY:
        value = getattr(import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_LAZY))
