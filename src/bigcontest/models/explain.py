"""SHAP 설명. LightGBM이 채택된 경우에만 쓴다 (configs/models.yaml adoption.shap).

SHAP 값은 **모델 출력의 설명**이다. 고립이나 결제 감소의 원인을 뜻하지 않는다.
"""

import numpy as np
import pandas as pd

LABEL = "모델 출력 설명, 원인 아님"


def shap_importance(model, X: pd.DataFrame) -> pd.DataFrame:
    """특징별 평균 |SHAP|. 결과 표에 LABEL을 붙인다."""
    import shap

    sv = shap.TreeExplainer(model).shap_values(X)
    out = pd.DataFrame({"feature": X.columns, "mean_abs_shap": np.abs(sv).mean(0)}).sort_values("mean_abs_shap", ascending=False)
    out["note"] = LABEL
    return out.reset_index(drop=True)
