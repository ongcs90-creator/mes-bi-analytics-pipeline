from fastapi import APIRouter, Depends, Query
from typing import List, Optional
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from app.services.dashboard_service import load_data_and_run_pipeline
import math

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

def clean_json_data(obj):
    if isinstance(obj, dict):
        return {k: clean_json_data(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_json_data(x) for x in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif pd.isna(obj):
        return None
    return obj


def get_filtered_data(
    factory: Optional[str] = None,
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = None,
    kubun: Optional[str] = None
):
    data = load_data_and_run_pipeline()
    df = data["enriched"].copy()
    
    # 1. Factory filter
    if factory and factory != "全工場":
        df = df[df["工場"] == factory]
    
    # 2. Kakouki filter
    if kakouki and kakouki in ["Y", "N"]:
        df = df[df["加工機"] == kakouki]
        
    # 3. Taisho filter
    if taisho and taisho in ["Y", "N"]:
        df = df[df["対象号機"] == taisho]
        
    # 3.5. Kubun filter (区分 / 分類)
    if kubun and kubun != "全て":
        df = df[df["分類"] == kubun]
        
    # 4. Machines filter
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None

    if machines:
        if isinstance(machines, str):
            mach_list = [m.strip() for m in machines.split(",") if m.strip()]
        elif len(machines) == 1 and "," in machines[0]:
            mach_list = [m.strip() for m in machines[0].split(",")]
        else:
            mach_list = machines
        df = df[df["機台"].isin(mach_list)]
        
    return df, data

@router.get("/filters")
def get_filters(
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    kubun: Optional[str] = None
):
    data = load_data_and_run_pipeline()
    df = data["enriched"].copy()
    
    # Available factories
    factories = ["全工場"] + sorted(df["工場"].dropna().unique().tolist())
    
    # Filter machine pool based on selections
    pool = df.copy()
    if factory and factory != "全工場":
        pool = pool[pool["工場"] == factory]
    if kakouki and kakouki in ["Y", "N"]:
        pool = pool[pool["加工機"] == kakouki]
    if taisho and taisho in ["Y", "N"]:
        pool = pool[pool["対象号機"] == taisho]
    if kubun and kubun != "全て":
        pool = pool[pool["分類"] == kubun]
        
    machines = sorted(pool["機台"].dropna().unique().tolist())
    
    return clean_json_data({
        "factories": factories,
        "machines": machines,
        "last_updated": data["last_updated"]
    })

def get_report_dates():
    today = date.today()
    # If today is 1st of month, report month is previous month
    if today.month > 1:
        report_year = today.year
        report_month = today.month - 1
    else:
        report_year = today.year - 1
        report_month = 12
        
    report_ym = f"{report_year}/{report_month:02d}"
    
    # Previous month relative to report month
    if report_month > 1:
        prev_year = report_year
        prev_month = report_month - 1
    else:
        prev_year = report_year - 1
        prev_month = 12
    prev_ym = f"{prev_year}/{prev_month:02d}"
    
    # YoY month
    yoy_ym = f"{report_year - 1}/{report_month:02d}"
    
    return report_ym, prev_ym, yoy_ym

def compute_kpi_dict(df_subset: pd.DataFrame) -> dict:
    if df_subset.empty:
        return {
            "生産性": None, "仕上m": 0, "平均ロット数": None, 
            "稼働率": None, "稼働時間h": 0, "段取時間件": None, "通し数": None
        }
    total_h  = df_subset["所要時間[h]"].sum()
    soto_h   = df_subset["稼働時間[h]"].sum()
    shima    = df_subset["仕上m"].sum() + df_subset["要検品m"].sum()
    lot_count = df_subset["生産No."].nunique()
    
    return {
        "生産性":      float(shima / total_h) if total_h > 0 else None,
        "仕上m":       float(shima),
        "平均ロット数": float(df_subset["投入m"].sum() / lot_count) if lot_count > 0 else None,
        "稼働率":      float(soto_h / total_h) if total_h > 0 else None,
        "稼働時間h":   float(soto_h),
        "段取時間件":  float(df_subset["段取時間"].mean()) if not df_subset["段取時間"].isna().all() else None,
        "通し数":      float(df_subset["通数"].sum()) if "通数" in df_subset.columns else None,
    }

@router.get("/summary")
def get_summary(
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = Query(None),
    kubun: Optional[str] = None
):
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None
    df_filtered, data = get_filtered_data(factory, kakouki, taisho, machines, kubun)
    report_ym, prev_ym, yoy_ym = get_report_dates()
    
    # Compute KPIs
    curr_kpi = compute_kpi_dict(df_filtered[df_filtered["month_key"] == report_ym])
    prev_kpi = compute_kpi_dict(df_filtered[df_filtered["month_key"] == prev_ym])
    yoy_kpi  = compute_kpi_dict(df_filtered[df_filtered["month_key"] == yoy_ym])
    
    # Restrict line charts to strictly display the last 13 months ending at report_ym
    report_dt = datetime.strptime(report_ym + "/01", "%Y/%m/%d")
    last13_months = []
    for i in range(12, -1, -1):
        m = (report_dt.month - 1 - i) % 12 + 1
        y = report_dt.year + (report_dt.month - 1 - i) // 12
        last13_months.append(f"{y}/{m:02d}")
        
    # Monthly trend series
    trend_series = []
    # If multiple machines are selected, we can break trend down by machine
    mach_filter = machines
    if machines and len(machines) == 1 and "," in machines[0]:
        mach_filter = [m.strip() for m in machines[0].split(",")]

    multi_machine = mach_filter and len(mach_filter) >= 2
    
    if multi_machine:
        for ym in last13_months:
            for mach in mach_filter:
                d = df_filtered[(df_filtered["month_key"] == ym) & (df_filtered["機台"] == mach)]
                kpis = compute_kpi_dict(d)
                trend_series.append({
                    "month": ym,
                    "machine": mach,
                    "生産性": kpis["生産性"],
                    "LSP": float(d["投入m"].sum() / (d["稼働時間[h]"].sum() * 60)) if d["稼働時間[h]"].sum() > 0 else None,
                    "ロット数": kpis["平均ロット数"]
                })
    else:
        for ym in last13_months:
            d = df_filtered[df_filtered["month_key"] == ym]
            kpis = compute_kpi_dict(d)
            trend_series.append({
                "month": ym,
                "生産性": kpis["生産性"],
                "LSP": float(d["投入m"].sum() / (d["稼働時間[h]"].sum() * 60)) if d["稼働時間[h]"].sum() > 0 else None,
                "ロット数": kpis["平均ロット数"]
            })
            
    # Heatmap data (稼働・段取りヒートマップ) - ordered exactly as requested:
    # 所要時間[h]、稼働時間、段取時間、前段取、後段取、段取時間(内部・外部ロスを除く)、平均LSP
    heat_data = []
    for ym in last13_months:
        d = df_filtered[df_filtered["month_key"] == ym]
        row = {"month": ym}
        row["所要時間[h]"] = float(d["所要時間[h]"].sum()) if not d["所要時間[h]"].isna().all() else None
        row["稼働時間"] = float(d["稼働時間"].mean()) if not d["稼働時間"].isna().all() else None
        row["段取時間"] = float(d["段取時間"].mean()) if not d["段取時間"].isna().all() else None
        row["前段取"] = float(d["前段取"].mean()) if not d["前段取"].isna().all() else None
        row["後段取"] = float(d["後段取"].mean()) if not d["後段取"].isna().all() else None
        row["段取時間(内部・外部ロスを除く)"] = float(d["段取時間(内部・外部ロスを除く)"].sum()) if not d["段取時間(内部・外部ロスを除く)"].isna().all() else None
        soto_h = d["稼働時間[h]"].sum()
        row["平均LSP"] = float(d["投入m"].sum() / (soto_h * 60)) if soto_h > 0 else None
        heat_data.append(row)
        
    return clean_json_data({
        "report_ym": report_ym,
        "prev_ym": prev_ym,
        "yoy_ym": yoy_ym,
        "kpis": {
            "current": curr_kpi,
            "previous": prev_kpi,
            "yoy": yoy_kpi
        },
        "trend": trend_series,
        "multi_machine": multi_machine,
        "heatmap": heat_data,
        "heatmap_metrics": ["所要時間[h]", "稼働時間", "段取時間", "前段取", "後段取", "段取時間(内部・外部ロスを除く)", "平均LSP"]
    })

@router.get("/analysis")
def get_analysis(
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = Query(None),
    kubun: Optional[str] = None,
    others_customer: Optional[str] = None
):
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None
    df_filtered, data = get_filtered_data(factory, kakouki, taisho, machines, kubun)
    
    # Separately filtered copy for the "others" charts
    df_others = df_filtered.copy()
    if others_customer and others_customer != "全て":
        df_others = df_others[df_others["得意先"] == others_customer]
    report_ym, prev_ym, _ = get_report_dates()
    
    # 1. Waterfall calculation
    df_curr = df_filtered[df_filtered["month_key"] == report_ym]
    df_prev = df_filtered[df_filtered["month_key"] == prev_ym]
    
    def get_subset_productivity(d):
        h = d["所要時間[h]"].sum()
        return (d["仕上m"].sum() + d["要検品m"].sum()) / h if h > 0 else 0.0
        
    prev_prod = get_subset_productivity(df_prev)
    curr_prod = get_subset_productivity(df_curr)
    total_diff = curr_prod - prev_prod
    
    # Filter variance table (target_variance_analysis for target machine focus, category_variance_analysis otherwise)
    use_taisho = (taisho in ["Y", "N"])
    vdf = data["target_variance_analysis"] if use_taisho else data["category_variance_analysis"]
    
    # Apply filter to variance dataframe
    vdf_sub = vdf[vdf["month_key"] == report_ym].copy()
    if factory and factory != "全工場":
        vdf_sub = vdf_sub[vdf_sub["工場"] == factory]
    if kakouki and kakouki in ["Y", "N"]:
        vdf_sub = vdf_sub[vdf_sub["加工機"] == kakouki]
    if taisho and taisho in ["Y", "N"]:
        vdf_sub = vdf_sub[vdf_sub["対象号機"] == taisho]
    if machines:
        mach_filter = machines
        if len(machines) == 1 and "," in machines[0]:
            mach_filter = [m.strip() for m in machines[0].split(",")]
        vdf_sub = vdf_sub[vdf_sub["機台"].isin(mach_filter)]
        
    raw_noryoku = float(vdf_sub["能率差異"].sum()) if not vdf_sub.empty else 0.0
    raw_kosei   = float(vdf_sub["構成差異"].sum()) if not vdf_sub.empty else 0.0
    
    # Scale proportions to match total_diff
    raw_total = raw_noryoku + raw_kosei
    if raw_total != 0:
        noryoku = total_diff * (raw_noryoku / raw_total)
        kosei   = total_diff * (raw_kosei / raw_total)
    else:
        noryoku = total_diff
        kosei   = 0.0
        
    waterfall = {
        "prev_prod": prev_prod,
        "noryoku": noryoku,
        "kosei": kosei,
        "curr_prod": curr_prod,
        "total_diff": total_diff
    }
    
    # 2. Customer Contribution table (scaled to match waterfall m/h values)
    scale_factor = total_diff / raw_total if raw_total != 0 else 1.0
    customer_contrib = []
    if not vdf_sub.empty:
        cust_grp = (
            vdf_sub.groupby("得意先", dropna=False)
            .agg(
                寄与度=("生産ギャップ", "sum"),
                能率差異=("能率差異", "sum"),
                構成差異=("構成差異", "sum")
            )
            .reset_index()
        )
        
        # Scale values to represent m/h contributions
        cust_grp["寄与度"] = cust_grp["寄与度"] * scale_factor
        cust_grp["能率差異"] = cust_grp["能率差異"] * scale_factor
        cust_grp["構成差異"] = cust_grp["構成差異"] * scale_factor
        
        cust_grp = cust_grp.sort_values("寄与度", ascending=True)
        
        for _, r in cust_grp.iterrows():
            customer_contrib.append({
                "customer": str(r["得意先"]),
                "contrib": float(r["寄与度"]) if pd.notna(r["寄与度"]) else 0.0,
                "noryoku": float(r["能率差異"]) if pd.notna(r["能率差異"]) else 0.0,
                "kosei": float(r["構成差異"]) if pd.notna(r["構成差異"]) else 0.0
            })
            
    # 3. Customer detail tables
    def get_customer_detail(df_in, ym):
        d = df_in[df_in["month_key"] == ym]
        if d.empty:
            return []
        grp = d.groupby("得意先", dropna=False)
        out = pd.DataFrame({
            "仕上m_h": grp.apply(lambda x: float((x["仕上m"].sum() + x["要検品m"].sum()) / x["所要時間[h]"].sum()) if x["所要時間[h]"].sum() > 0 else None),
            "生産件数": grp["生産No."].nunique(),
            "LSP": grp.apply(lambda x: float(x["投入m"].sum() / (x["稼働時間[h]"].sum() * 60)) if x["稼働時間[h]"].sum() > 0 else None),
            "段取時間": grp["段取時間"].mean(),
            "ロット数": grp.apply(lambda x: float(x["投入m"].sum() / x["生産No."].nunique()) if x["生産No."].nunique() > 0 else None),
            "所要時間_sum": grp["所要時間[h]"].sum()
        }).reset_index()
        total_lots = out["生産件数"].sum()
        total_hours = out["所要時間_sum"].sum()
        out["生産件数_pct"] = (out["生産件数"] / total_lots * 100).round(1) if total_lots > 0 else 0.0
        out["所要時間_pct"] = (out["所要時間_sum"] / total_hours * 100).round(1) if total_hours > 0 else 0.0
        out = out.sort_values("得意先", ascending=True)
        return out.to_dict(orient="records")

    cust_detail_curr = get_customer_detail(df_filtered, report_ym)
    cust_detail_prev = get_customer_detail(df_filtered, prev_ym)
    
    # 4. Roll length distribution change (巻m分布変化)
    BIN_ORDER = ["0-1000", "1001-2000", "2001-3000", "3001-4000", "4001-5000",
                 "5001-6000", "6001-7000", "7001-8000", "8001-9000", "9001-10000", ">10000"]
                 
    def get_bin_counts(df_in, ym):
        d = df_in[(df_in["month_key"] == ym) & df_in["巻m(bin)_label"].notna()]
        if d.empty:
            return pd.Series(0, index=BIN_ORDER)
        return d["巻m(bin)_label"].value_counts().reindex(BIN_ORDER, fill_value=0)
        
    cnt_curr = get_bin_counts(df_others, report_ym)
    cnt_prev = get_bin_counts(df_others, prev_ym)
    cnt_diff = cnt_curr - cnt_prev
    
    maki_dist = [{"bin": b, "change": int(cnt_diff[b])} for b in BIN_ORDER]
    
    # 5. Usage pie chart (用途別構成比)
    def get_yoto_shares(ym):
        d = df_others[(df_others["month_key"] == ym) & df_others["用途"].notna()]
        if d.empty:
            return []
        cnt = d.groupby("用途")["生産No."].nunique().reset_index(name="件数")
        cnt = cnt.nlargest(10, "件数")
        return cnt.to_dict(orient="records")
        
    yoto_curr = get_yoto_shares(report_ym)
    yoto_prev = get_yoto_shares(prev_ym)
    
    # 6. Classification Trend (所要時間[h] split by テスト vs リピート for 13 months)
    report_dt = datetime.strptime(report_ym + "/01", "%Y/%m/%d")
    last13_months = []
    for i in range(12, -1, -1):
        m = (report_dt.month - 1 - i) % 12 + 1
        y = report_dt.year + (report_dt.month - 1 - i) // 12
        last13_months.append(f"{y}/{m:02d}")
        
    class_trend = []
    for ym in last13_months:
        d = df_others[df_others["month_key"] == ym]
        sum_repeat = float(d[d["分類"] == "リピート"]["所要時間[h]"].sum())
        sum_test = float(d[d["分類"] == "テスト"]["所要時間[h]"].sum())
        class_trend.append({
            "month": ym,
            "リピート": sum_repeat,
            "テスト": sum_test
        })
        
    total_jobs_curr = int(df_others[df_others["month_key"] == report_ym]["生産No."].nunique())
    total_jobs_prev = int(df_others[df_others["month_key"] == prev_ym]["生産No."].nunique())
    
    return clean_json_data({
        "report_ym": report_ym,
        "prev_ym": prev_ym,
        "waterfall": waterfall,
        "customer_contrib": customer_contrib,
        "customer_detail_curr": cust_detail_curr,
        "customer_detail_prev": cust_detail_prev,
        "maki_dist": maki_dist,
        "yoto_curr": yoto_curr,
        "yoto_prev": yoto_prev,
        "use_taisho": use_taisho,
        "classification_trend": class_trend,
        "total_jobs_curr": total_jobs_curr,
        "total_jobs_prev": total_jobs_prev
    })

@router.get("/daily-mgmt")
def get_daily_mgmt(
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = Query(None),
    kubun: Optional[str] = None
):
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None
    df_filtered, data = get_filtered_data(factory, kakouki, taisho, machines, kubun)
    
    today = date.today()
    
    # Determine the latest date with records strictly before today to handle weekends and holidays (excluding Saturday and Sunday)
    latest_workday = None
    if "加工終了日" in df_filtered.columns:
        conv = pd.to_datetime(df_filtered["加工終了日"], errors="coerce")
        # Exclude Saturday (5) and Sunday (6)
        dates_less_than_today = conv[(conv.dt.date < today) & (conv.dt.dayofweek < 5)].dt.date
        if not dates_less_than_today.empty:
            latest_workday = dates_less_than_today.max()
    elif "加工終了日時" in df_filtered.columns:
        conv = pd.to_datetime(df_filtered["加工終了日時"], errors="coerce")
        # Exclude Saturday (5) and Sunday (6)
        dates_less_than_today = conv[(conv.dt.date < today) & (conv.dt.dayofweek < 5)].dt.date
        if not dates_less_than_today.empty:
            latest_workday = dates_less_than_today.max()
            
    if latest_workday is None:
        latest_workday = today - timedelta(days=1)
        
    yesterday = latest_workday
    current_ym = f"{today.year}/{today.month:02d}"
    
    # Elapsed calendar working days
    mtd_work = 0
    total_work = 0
    workdays_df = data["workdays_df"]
    if workdays_df is not None and not workdays_df.empty:
        wdf = workdays_df.copy()
        wdf["日付"] = pd.to_datetime(wdf["日付"], errors="coerce")
        month_days = wdf[(wdf["日付"].dt.year == today.year) & (wdf["日付"].dt.month == today.month)]
        total_work = int(month_days["出勤日"].sum())
        elapsed = month_days[month_days["日付"].dt.date < today]
        mtd_work = int(elapsed["出勤日"].sum())
        
    progress_pct = (mtd_work / total_work * 100) if total_work > 0 else 0.0
    
    # Compute current month KPIs and compare with report month
    report_ym, _, _ = get_report_dates()
    
    def compute_mtd_kpis(d):
        if d.empty:
            return {
                "生産性": None, "仕上m": 0, "所要時間h": 0, "ロット数": None,
                "LSP": None, "平均色数": None, "段取時間件": None, "内部ロス": 0.0
            }
        total_h  = d["所要時間[h]"].sum()
        soto_h   = d["稼働時間[h]"].sum()
        shima    = d["仕上m"].sum() + d["要検品m"].sum()
        color_col = next((c for c in ["色数", "色数２"] if c in d.columns), None)
        lot_count = d["生産No."].nunique()
        
        return {
            "生産性": float(shima / total_h) if total_h > 0 else None,
            "仕上m": float(shima),
            "所要時間h": float(total_h),
            "ロット数": float(d["投入m"].sum() / lot_count) if lot_count > 0 else None,
            "LSP": float(d["投入m"].sum() / (soto_h * 60)) if soto_h > 0 else None,
            "平均色数": float(d[color_col].mean()) if color_col and not d[color_col].isna().all() else None,
            "段取時間件": float(d["段取時間"].mean()) if not d["段取時間"].isna().all() else None,
            "内部ロス": float(d["内部ロス時間"].sum()) if "内部ロス時間" in d.columns else 0.0,
        }

    curr_kpi = compute_mtd_kpis(df_filtered[df_filtered["month_key"] == current_ym])
    prev_kpi = compute_mtd_kpis(df_filtered[df_filtered["month_key"] == report_ym])
    
    # Calculate YTY (same month last year MTD)
    try:
        parts = current_ym.split("/")
        yty_ym = f"{int(parts[0]) - 1}/{parts[1]}"
    except Exception:
        yty_ym = f"{today.year - 1}/{today.month:02d}"
        
    df_yty = df_filtered[df_filtered["month_key"] == yty_ym]
    if not df_yty.empty:
        if "加工終了日" in df_yty.columns:
            conv_yty = pd.to_datetime(df_yty["加工終了日"], errors="coerce")
            df_yty = df_yty[conv_yty.dt.day <= yesterday.day]
        elif "加工終了日時" in df_yty.columns:
            conv_yty = pd.to_datetime(df_yty["加工終了日時"], errors="coerce")
            df_yty = df_yty[conv_yty.dt.day <= yesterday.day]
            
    yty_kpi = compute_mtd_kpis(df_yty)
    
    # Yesterday's details
    df_yest = pd.DataFrame()
    if "加工終了日" in df_filtered.columns:
        conv = pd.to_datetime(df_filtered["加工終了日"], errors="coerce")
        df_yest = df_filtered[conv.dt.date == yesterday]
    elif "加工終了日時" in df_filtered.columns:
        conv = pd.to_datetime(df_filtered["加工終了日時"], errors="coerce")
        df_yest = df_filtered[conv.dt.date == yesterday]

    yest_summary = {}
    if not df_yest.empty:
        lossm = df_yest["ロスm"].sum() if "ロスm" in df_yest.columns else 0.0
        yest_summary = {
            "仕上m": float(df_yest["仕上m"].sum()),
            "要検品m": float(df_yest["要検品m"].sum()),
            "ロスm": float(lossm),
            "投入m": float(df_yest["投入m"].sum()),
            "稼働時間": float(df_yest["稼働時間[h]"].sum()) if "稼働時間[h]" in df_yest.columns else 0.0,
            "段取時間": float(df_yest["段取時間"].sum()) if "段取時間" in df_yest.columns else 0.0,
            "内部ロス": float(df_yest["内部ロス時間"].sum() / 60) if "内部ロス時間" in df_yest.columns else 0.0,
            "外部ロス": float(df_yest["外部ロス時間"].sum() / 60) if "外部ロス時間" in df_yest.columns else 0.0
        }
        
    # MTD Customer details
    df_curr = df_filtered[df_filtered["month_key"] == current_ym]
    mtd_customers = []
    if not df_curr.empty:
        grp = df_curr.groupby("得意先", dropna=False)
        out = pd.DataFrame({
            "得意先": grp.groups.keys(),
            "1時間当り仕上m": grp.apply(lambda x: float((x["仕上m"].sum() + x["要検品m"].sum()) / x["所要時間[h]"].sum()) if x["所要時間[h]"].sum() > 0 else None),
            "平均LSP": grp.apply(lambda x: float(x["投入m"].sum() / (x["稼働時間[h]"].sum() * 60)) if x["稼働時間[h]"].sum() > 0 else None),
            "平均段取時間": grp["段取時間"].mean(),
            "平均ロット数": grp.apply(lambda x: float(x["投入m"].sum() / x["生産No."].nunique()) if x["生産No."].nunique() > 0 else None),
            "生産件数": grp["生産No."].nunique(),
        }).reset_index(drop=True)
        total_lots = out["生産件数"].sum()
        out["生産件数_pct"] = (out["生産件数"] / total_lots * 100).round(1) if total_lots > 0 else 0.0
        out = out.sort_values("生産件数_pct", ascending=False)
        mtd_customers = out.to_dict(orient="records")

    # Stop Reason and Work Time Detail
    work_df = data["work_df"]
    yesterday_loss_summary = []
    mtd_worktype_summary = []
    
    if work_df is not None and not work_df.empty:
        wf = work_df.copy()
        date_col = "終了日" if "終了日" in wf.columns else "終了日時"
        wf[date_col] = pd.to_datetime(wf[date_col], errors="coerce")
        
        # 1. Calculate Yesterday Loss Summary (Table 4)
        yest_work = wf[wf[date_col].dt.date == yesterday]
        if "工場" in yest_work.columns and factory and factory != "全工場":
            yest_work = yest_work[yest_work["工場"] == factory]
        if machines and "機台" in yest_work.columns:
            mach_filter = machines
            if len(machines) == 1 and "," in machines[0]:
                mach_filter = [m.strip() for m in machines[0].split(",")]
            yest_work = yest_work[yest_work["機台"].isin(mach_filter)]
            
        yest_work = yest_work[yest_work["ロス内容"].isin(["内部ロス", "外部ロス"])]
        
        if not yest_work.empty and "所要時間" in yest_work.columns:
            group_cols = [c for c in ["ロス内容", "作業内容", "停止理由"] if c in yest_work.columns]
            if group_cols:
                summary = yest_work.groupby(group_cols, dropna=False)["所要時間"].sum().reset_index()
                summary["時間h"] = (summary["所要時間"] / 60).round(2)
                summary = summary.sort_values("時間h", ascending=False)
                yesterday_loss_summary = summary.to_dict(orient="records")

        # 2. Calculate MTD Work Type Summary (Table 5)
        curr_ym_dt = pd.to_datetime(current_ym + "/01", format="%Y/%m/%d")
        curr_work = wf[(wf[date_col].dt.year == curr_ym_dt.year) & (wf[date_col].dt.month == curr_ym_dt.month)]
        if "工場" in curr_work.columns and factory and factory != "全工場":
            curr_work = curr_work[curr_work["工場"] == factory]
        if machines and "機台" in curr_work.columns:
            mach_filter = machines
            if len(machines) == 1 and "," in machines[0]:
                mach_filter = [m.strip() for m in machines[0].split(",")]
            curr_work = curr_work[curr_work["機台"].isin(mach_filter)]
            
        if not curr_work.empty and "所要時間" in curr_work.columns:
            group_cols = [c for c in ["ロス内容", "作業内容"] if c in curr_work.columns]
            if group_cols:
                summary = curr_work.groupby(group_cols, dropna=False)["所要時間"].sum().reset_index()
                summary["時間h"] = (summary["所要時間"] / 60).round(2)
                total_hours = summary["時間h"].sum()
                summary["比率_pct"] = (summary["時間h"] / total_hours * 100).round(1) if total_hours > 0 else 0.0
                summary = summary.sort_values("時間h", ascending=False)
                mtd_worktype_summary = summary.to_dict(orient="records")

    return clean_json_data({
        "today": today.strftime('%Y年%m月%d日'),
        "yesterday": yesterday.strftime('%m/%d'),
        "current_ym": current_ym,
        "report_ym": report_ym,
        "workdays": {
            "mtd_work": mtd_work,
            "total_work": total_work,
            "progress_pct": progress_pct
        },
        "kpis": {
            "current": curr_kpi,
            "previous": prev_kpi,
            "yty": yty_kpi
        },
        "yesterday_summary": yest_summary,
        "mtd_customers": mtd_customers,
        "yesterday_loss_summary": yesterday_loss_summary,
        "mtd_worktype_summary": mtd_worktype_summary
    })

@router.get("/trend")
def get_trend(
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = Query(None),
    metric: str = "生産性",
    kubun: Optional[str] = None
):
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None
    df_filtered, data = get_filtered_data(factory, kakouki, taisho, machines, kubun)
    report_ym, _, _ = get_report_dates()
    
    # Restrict line charts to display the last 13 months ending at report_ym
    report_dt = datetime.strptime(report_ym + "/01", "%Y/%m/%d")
    avail_months = []
    for i in range(12, -1, -1):
        m = (report_dt.month - 1 - i) % 12 + 1
        y = report_dt.year + (report_dt.month - 1 - i) // 12
        avail_months.append(f"{y}/{m:02d}")
    
    # Aggregation
    trend_series = []
    for ym in avail_months:
        d = df_filtered[df_filtered["month_key"] == ym]
        if d.empty:
            continue
        total_h = d["所要時間[h]"].sum()
        soto_h  = d["稼働時間[h]"].sum()
        shima   = d["仕上m"].sum() + d["要検品m"].sum()
        lot_count = d["生産No."].nunique()
        trend_series.append({
            "month": ym,
            "仕上m": float(shima),
            "所要時間h": float(total_h),
            "生産性": float(shima / total_h) if total_h > 0 else None,
            "稼働率": float(soto_h / total_h) if total_h > 0 else None,
            "LSP": float(d["投入m"].sum() / (soto_h * 60)) if soto_h > 0 else None,
            "段取時間件": float(d["段取時間"].mean()) if not d["段取時間"].isna().all() else None,
        })
        
    # Get targets for the indicator
    target_df = data["target_df"]
    
    # Calculate target values for each month in trend
    col_map = {"稼働率": "稼働率", "LSP": "LS/分", "段取時間件": "版替時間/件"}
    tgt_col = col_map.get(metric)
    
    targets = {}
    if tgt_col and target_df is not None and not target_df.empty:
        # Determine factories to filter
        if factory and factory != "全工場":
            sel_facs = [factory]
        else:
            sel_facs = sorted(df_filtered["工場"].dropna().unique().tolist())
            
        for ym in avail_months:
            ym_dt = pd.to_datetime(ym + "/01", format="%Y/%m/%d", errors="coerce")
            sub = target_df[target_df["工場"].isin(sel_facs) & (target_df["日付"] == ym_dt)]
            if not sub.empty and tgt_col in sub.columns:
                vals = sub[tgt_col].dropna()
                targets[ym] = float(vals.mean()) if len(vals) > 0 else None
                
    # Combine trend and targets
    result = []
    for item in trend_series:
        ym = item["month"]
        val = item.get(metric)
        tgt_val = targets.get(ym)
        
        # If metric is 稼働率, convert to percentage for visualization (matches streamlit 04)
        if metric == "稼働率" and val is not None:
            val = val * 100
        if metric == "稼働率" and tgt_val is not None:
            tgt_val = tgt_val * 100
            
        result.append({
            "month": ym,
            "value": val,
            "target": tgt_val
        })
        
    return clean_json_data({
        "report_ym": report_ym,
        "metric": metric,
        "series": result
    })

@router.get("/customer-trend")
def get_customer_trend(
    customer: str,
    factory: Optional[str] = "全工場",
    kakouki: Optional[str] = None,
    taisho: Optional[str] = None,
    machines: Optional[List[str]] = Query(None),
    kubun: Optional[str] = None
):
    if machines is not None and not isinstance(machines, list) and not isinstance(machines, str):
        machines = None
    df_filtered, data = get_filtered_data(factory, kakouki, taisho, machines, kubun)
    
    # Filter for specific customer
    df_cust = df_filtered[df_filtered["得意先"] == customer]
    
    # Get last 13 months
    report_ym, _, _ = get_report_dates()
    report_dt = datetime.strptime(report_ym + "/01", "%Y/%m/%d")
    last13_months = []
    for i in range(12, -1, -1):
        m = (report_dt.month - 1 - i) % 12 + 1
        y = report_dt.year + (report_dt.month - 1 - i) // 12
        last13_months.append(f"{y}/{m:02d}")
        
    color_col = next((c for c in ["色数", "色数２"] if c in df_cust.columns), None)
    
    trend = []
    for ym in last13_months:
        d = df_cust[df_cust["month_key"] == ym]
        if d.empty:
            trend.append({
                "month": ym,
                "生産性": None, "LSP": None,
                "平均色数": None, "平均段取時間": None,
                "加工本数": 0, "平均投入m": None
            })
            continue
            
        h = d["所要時間[h]"].sum()
        prod = float((d["仕上m"].sum() + d["要検品m"].sum()) / h) if h > 0 else None
        
        run_h = d["稼働時間[h]"].sum()
        lsp = float(d["投入m"].sum() / (run_h * 60)) if run_h > 0 else None
        
        color_val = float(d[color_col].mean()) if color_col and not d[color_col].isna().all() else None
        setup_val = float(d["段取時間"].mean()) if not d["段取時間"].isna().all() else None
        
        honsu_val = float(d["加工本数"].sum()) if "加工本数" in d.columns else 0.0
        m_val = float(d["投入m"].mean()) if "投入m" in d.columns else None
        
        trend.append({
            "month": ym,
            "生産性": prod,
            "LSP": lsp,
            "平均色数": color_val,
            "平均段取時間": setup_val,
            "加工本数": honsu_val,
            "平均投入m": m_val
        })
        
    return clean_json_data({
        "customer": customer,
        "trend": trend
    })
