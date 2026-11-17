"""
Trains the Kickwise V1 random forest (UNDER 2.5) from the repo's own data and
saves kickwise_random_forest_v1.joblib. Run by the "Train Kickwise V1 model"
workflow so the model is built with the SAME library versions it is later
loaded with (see requirements_model.txt).

Inputs (repo root): analysis.csv (and scores.csv, used to fill missing results)
Outputs: kickwise_random_forest_v1.joblib, kickwise_v1_report.txt

Same settings as the original script: B46/B120/C120/D64 as categories, blank ->
"MISSING", chronological 70/30 test, then a final fit on ALL scored matches.
"""
import os
import sys
import numpy as np
import pandas as pd
import joblib

from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score, log_loss, brier_score_loss

FEATURES = ["B46", "B120", "C120", "D64"]
MODEL_FILE = "kickwise_random_forest_v1.joblib"
REPORT_FILE = "kickwise_v1_report.txt"
MIN_ROWS = 300

lines = []
def say(text=""):
    print(text)
    lines.append(str(text))

df = pd.read_csv("analysis.csv")
df.columns = df.columns.str.strip()

# fill missing results from scores.csv when available
if os.path.exists("scores.csv"):
    sc = pd.read_csv("scores.csv")
    sc.columns = sc.columns.str.strip()
    keys = ["date", "home", "away"]
    if all(k in sc.columns for k in keys + ["hg", "ag"]) and all(k in df.columns for k in keys):
        base = df.drop(columns=[c for c in ["hg", "ag"] if c in df.columns])
        df = base.merge(sc[keys + ["hg", "ag"]].drop_duplicates(keys), on=keys, how="left")

for col in ["hg", "ag"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df["date_dt"] = pd.to_datetime(df["date"], errors="coerce")
df = df.dropna(subset=["date_dt", "hg", "ag"])
if "time" not in df.columns:
    df["time"] = ""
df = df.sort_values(["date_dt", "time"]).reset_index(drop=True)

if len(df) < MIN_ROWS:
    say(f"Only {len(df)} scored matches (need {MIN_ROWS}) — not training.")
    sys.exit(1)

df["UNDER_25"] = ((df["hg"] + df["ag"]) < 2.5).astype(int)
for col in FEATURES:
    df[col] = df[col].fillna("MISSING").astype(str).str.strip()
    df.loc[df[col].isin(["", "nan", "None", "N/A"]), col] = "MISSING"

X, Y = df[FEATURES], df["UNDER_25"]
split = int(len(df) * 0.70)

def make_model():
    pre = ColumnTransformer([(
        "indicators",
        Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("encoding", OneHotEncoder(handle_unknown="ignore")),
        ]),
        FEATURES,
    )])
    return Pipeline([
        ("preprocessor", pre),
        ("classifier", RandomForestClassifier(
            n_estimators=700, max_depth=5, min_samples_leaf=25,
            max_features="sqrt", class_weight="balanced_subsample",
            random_state=42, n_jobs=-1)),
    ])

say(f"Scored matches: {len(df)}  ({df['date'].iloc[0]} -> {df['date'].iloc[-1]})")
test_model = make_model().fit(X.iloc[:split], Y.iloc[:split])
p = test_model.predict_proba(X.iloc[split:])[:, 1]
y = Y.iloc[split:]
say(f"\nHELD-OUT TEST (last 30%, {len(y)} matches, {df['date'].iloc[split]} -> {df['date'].iloc[-1]})")
say(f"  Accuracy {accuracy_score(y, (p >= .5).astype(int)):.3f} | Balanced {balanced_accuracy_score(y, (p >= .5).astype(int)):.3f}"
    f" | AUC {roc_auc_score(y, p):.3f} | LogLoss {log_loss(y, p):.3f} | Brier {brier_score_loss(y, p):.4f}")
say(f"  Under rate in the test period: {y.mean():.1%}  | probability range {p.min():.2f}-{p.max():.2f}")
say("\n  Calls on the held-out matches (P = model probability of UNDER 2.5):")
for lo, label in [(0.47, "P >= 0.47"), (0.58, "P >= 0.58"), (0.60, "P >= 0.60"), (0.62, "P >= 0.62")]:
    m = p >= lo
    if m.any():
        say(f"    {label}: {int(m.sum()):4d} matches ({m.mean():.0%}) -> went under {y[m].mean():.0%}")
for hi, label in [(0.45, "P <= 0.45"), (0.40, "P <= 0.40")]:
    m = p <= hi
    if m.any():
        say(f"    {label}: {int(m.sum()):4d} matches ({m.mean():.0%}) -> went OVER {(1 - y[m]).mean():.0%}")

final_model = make_model().fit(X, Y)          # final fit on ALL scored matches
joblib.dump(final_model, MODEL_FILE)
say(f"\nSaved {MODEL_FILE} (trained on all {len(df)} scored matches).")
with open(REPORT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
