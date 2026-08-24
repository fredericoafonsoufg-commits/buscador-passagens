 
import os, json, hashlib, statistics, hmac
from urllib.parse import quote
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import altair as alt
try:
    import airportsdata
except Exception:
    airportsdata = None
import requests
import streamlit as st
import base64
from io import BytesIO
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak, KeepTogether
)

st.set_page_config(
    page_title="Frederico Travel Tools",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Limpa resultados antigos ao iniciar uma nova sessão do aplicativo.
if "_sessao_ftt_inicializada" not in st.session_state:
    for _k in [
        "rank", "uso", "retornos", "preco_ref", "ultima_pesquisa",
        "price_insights_raw", "_ultimo_ponto_salvo", "retorno_sel_key"
    ]:
        st.session_state.pop(_k, None)
    st.session_state["_sessao_ftt_inicializada"] = True


st.html("""
<style>
[data-testid="stToolbar"] {display:none !important;}
[data-testid="stDecoration"] {display:none !important;}
[data-testid="stStatusWidget"] {display:none !important;}
#MainMenu {visibility:hidden !important;}
</style>
""")


st.html("""
<style>
/* Sidebar: abre expandida; se for recolhida, o botão de reabrir permanece visível. */
[data-testid="stSidebar"] {
    min-width: 365px !important;
    max-width: 365px !important;
    background: #ffffff !important;
    border-right: 1px solid #e5ebf3 !important;
}
[data-testid="stSidebar"] > div:first-child {
    width: 365px !important;
}
[data-testid="stSidebar"] .block-container {
    padding: 16px 18px 18px !important;
}
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
}

/* Oculta elementos administrativos do Streamlit Cloud que não fazem parte do app. */
[data-testid="stAppDeployButton"],
[data-testid="stToolbarActions"],
[data-testid="stStatusWidget"],
div[class*="viewerBadge"],
div[class*="ViewerBadge"],
div[class*="manage-app"],
div[class*="ManageApp"] {
    display: none !important;
}

/* Mantém o cabeçalho discreto sem esconder o botão de reabrir a lateral. */
header[data-testid="stHeader"] {
    background: transparent !important;
}
</style>
""")


st.html("""
<style>
/* Sidebar limpa e estável: usa a largura nativa do Streamlit. */
[data-testid="stSidebar"] {
    background:#ffffff !important;
    border-right:1px solid #e5ebf3 !important;
}
[data-testid="stSidebar"] .block-container {
    padding-top:1rem !important;
    padding-left:1.1rem !important;
    padding-right:1.1rem !important;
}
/* Mantém os controles nativos para recolher/reabrir a lateral. */
[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
    visibility:visible !important;
    opacity:1 !important;
}
/* Conteúdo principal */
.block-container {
    padding-top:0.8rem !important;
    max-width:1500px !important;
}
</style>
""")


st.html("""
<style>
/* Frederico Travel Tools: menu lateral fixo no desktop. */
[data-testid="stSidebarCollapseButton"] {
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
}
/* Caso o Streamlit injete variações do mesmo controle dentro da sidebar. */
[data-testid="stSidebar"] button[aria-label*="sidebar" i],
[data-testid="stSidebar"] button[title*="sidebar" i] {
    display: none !important;
}
</style>
""")


st.html("""
<style>
/* Oculta controles técnicos de cache/toolbar do Streamlit da interface do usuário. */
[data-testid="stToolbar"] {display:none !important;}
[data-testid="stStatusWidget"] {display:none !important;}
#MainMenu {visibility:hidden !important;}
</style>
""")


st.html("""
<style>
[data-testid="stToolbar"],
[data-testid="stStatusWidget"],
[data-testid="stMainMenu"],
#MainMenu {
    display:none !important;
    visibility:hidden !important;
}
</style>
""")

SERPAPI_URL = "https://serpapi.com/search.json"
BASE = Path.cwd()
DATA_DIR = BASE / "dados_usuario"
DATA_DIR.mkdir(exist_ok=True)
SALDOS_FILE = DATA_DIR / "meus_saldos.json"
CACHE_DIR = BASE / "cache_buscas"
CACHE_DIR.mkdir(exist_ok=True)
HIST_FILE = DATA_DIR / "historico_precos.csv"

def _secret_or_env(name, default=""):
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()

def api_key():
    return _secret_or_env("SERPAPI_API_KEY")

def brl(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "—"

def pts(v):
    try:
        return f"{int(v):,}".replace(",", ".")
    except:
        return "0"

def data_br(v):
    if not v:
        return ""
    try:
        if isinstance(v, (date, datetime)):
            return v.strftime("%d/%m/%Y")
        s = str(v)
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z",""))
            return dt.strftime("%d/%m/%Y %H:%M")
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            resto = s[10:]
            if resto:
                try:
                    dt = datetime.fromisoformat(s.replace("Z",""))
                    return dt.strftime("%d/%m/%Y %H:%M")
                except:
                    pass
            return dt.strftime("%d/%m/%Y")
    except:
        pass
    return str(v)

def load_saldos():
    if "saldos_usuario" not in st.session_state:
        def _num_secret(nome, padrao):
            try:
                return int(float(_secret_or_env(nome, str(padrao))))
            except Exception:
                return int(padrao)

        st.session_state["saldos_usuario"] = {
            "LATAM Pass": _num_secret("LATAM_BALANCE", 2284),
            "Smiles": _num_secret("SMILES_BALANCE", 51100),
            "Azul Fidelidade": _num_secret("AZUL_BALANCE", 0),
        }
    return dict(st.session_state["saldos_usuario"])

def save_saldos(d):
    st.session_state["saldos_usuario"] = {
        "LATAM Pass": int(d["LATAM Pass"]),
        "Smiles": int(d["Smiles"]),
        "Azul Fidelidade": int(d["Azul Fidelidade"]),
    }

def codigos(txt):
    out = []
    for x in txt.replace(";", ",").split(","):
        x = x.strip().upper()
        if x and x not in out:
            out.append(x)
    return out

def cache_key(params):
    safe = {k:v for k,v in params.items() if k != "api_key"}
    raw = json.dumps(safe, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()

CACHE_TTL_MINUTOS = 30

def consulta(params):
    """
    Reaproveita automaticamente pesquisas idênticas recentes.
    Após CACHE_TTL_MINUTOS, faz uma nova consulta para atualizar os preços.
    """
    f = CACHE_DIR / f"{cache_key(params)}.json"

    if f.exists():
        try:
            idade_segundos = datetime.now().timestamp() - f.stat().st_mtime
            if idade_segundos <= CACHE_TTL_MINUTOS * 60:
                return json.loads(f.read_text(encoding="utf-8")), True
        except Exception:
            pass

    r = requests.get(SERPAPI_URL, params=params, timeout=60)
    if not r.ok:
        raise RuntimeError(f"Erro SerpApi ({r.status_code}): {r.text[:400]}")

    d = r.json()
    if d.get("error"):
        raise RuntimeError(d["error"])

    f.write_text(
        json.dumps(d, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return d, False

def params_base(orig, dest, ida, volta, adultos, cabine, stops):
    p = {
        "engine": "google_flights",
        "api_key": api_key(),
        "departure_id": ",".join(orig),
        "arrival_id": ",".join(dest),
        "outbound_date": ida.isoformat(),
        "type": 1 if volta else 2,
        "travel_class": cabine,
        "adults": adultos,
        "stops": stops,
        "currency": "BRL",
        "gl": "br",
        "hl": "pt"
    }
    if volta:
        p["return_date"] = volta.isoformat()
    return p

def all_items(d):
    return (d.get("best_flights") or []) + (d.get("other_flights") or [])

def summarize(x):
    fs = x.get("flights") or []
    if not fs:
        return None

    cias, nums = [], []
    for f in fs:
        if f.get("airline") and f["airline"] not in cias:
            cias.append(f["airline"])
        if f.get("flight_number"):
            nums.append(f["flight_number"])

    dep = fs[0].get("departure_airport") or {}
    arr = fs[-1].get("arrival_airport") or {}
    dur = x.get("total_duration")

    return {
        "preco": x.get("price"),
        "origem": dep.get("id", ""),
        "destino": arr.get("id", ""),
        "cias": " + ".join(cias),
        "saida": dep.get("time", ""),
        "chegada": arr.get("time", ""),
        "escalas": max(len(fs)-1, 0),
        "duracao": f"{dur//60}h {dur%60:02d}min" if isinstance(dur, int) else "",
        "voos": " / ".join(nums),
        "token": x.get("departure_token", "")
    }

def _hora_voo(valor):
    txt = str(valor or "").strip()
    # Aceita "29/01/2027 11:45" e também "2027-01-29 11:45".
    if " " in txt:
        return txt.rsplit(" ", 1)[-1][:5]
    return txt[:5]


def _normaliza_voos(valor):
    return " / ".join(
        p.strip().upper().replace("  ", " ")
        for p in str(valor or "").split("/")
        if p.strip()
    )


def mapa_precos_so_trecho(origens, destinos, datas, adultos, cabine, stops):
    """
    Consulta tarifas avulsas (somente ida).
    Guarda chaves alternativas para conseguir identificar o mesmo voo mesmo
    quando o Google Flights muda a forma de escrever o número do voo.
    """
    datas = sorted(set(d for d in datas if d))
    mapa = {}
    if not datas:
        return mapa

    def _grava(chave, preco):
        if chave not in mapa or preco < mapa[chave]:
            mapa[chave] = preco

    def _consulta_data(data_voo):
        p = params_base(origens, destinos, data_voo, None, int(adultos), cabine, stops)
        try:
            d, _ = consulta(p)
            return data_voo, d
        except Exception:
            return data_voo, None

    with ThreadPoolExecutor(max_workers=min(4, len(datas))) as executor:
        futuros = [executor.submit(_consulta_data, d) for d in datas]
        for futuro in as_completed(futuros):
            data_voo, dados = futuro.result()
            if not dados:
                continue
            for item in all_items(dados):
                s = summarize(item)
                if not s or not isinstance(s.get("preco"), (int, float)):
                    continue

                data_txt = data_br(data_voo)
                origem = str(s.get("origem") or "")
                destino = str(s.get("destino") or "")
                voos = _normaliza_voos(s.get("voos"))
                cia = str(s.get("cias") or "").strip().upper()
                hora = _hora_voo(s.get("saida"))
                duracao = str(s.get("duracao") or "").strip()
                preco = float(s["preco"])

                # 1) correspondência exata pelo(s) número(s) do voo.
                if voos:
                    _grava(("voos", data_txt, origem, destino, voos), preco)

                # 2) fallback forte: data + rota + companhia + horário + duração.
                if hora:
                    _grava(("hora_cia_dur", data_txt, origem, destino, hora, cia, duracao), preco)
                    _grava(("hora_cia", data_txt, origem, destino, hora, cia), preco)
                    _grava(("hora", data_txt, origem, destino, hora), preco)

                # 3) fallback final: menor tarifa avulsa daquela rota/data.
                _grava(("rota_data", data_txt, origem, destino), preco)

    return mapa


def preco_so_trecho(
    mapa, data_txt, origem, destino, voos,
    companhia=None, saida=None, duracao=None
):
    data_txt = str(data_txt or "")
    origem = str(origem or "")
    destino = str(destino or "")
    voos_norm = _normaliza_voos(voos)
    cia = str(companhia or "").strip().upper()
    hora = _hora_voo(saida)
    duracao = str(duracao or "").strip()

    chaves = []
    if voos_norm:
        chaves.append(("voos", data_txt, origem, destino, voos_norm))
    if hora:
        chaves.extend([
            ("hora_cia_dur", data_txt, origem, destino, hora, cia, duracao),
            ("hora_cia", data_txt, origem, destino, hora, cia),
            ("hora", data_txt, origem, destino, hora),
        ])

    for chave in chaves:
        if chave in mapa:
            return mapa[chave]

    # Se o voo exato não existir na pesquisa avulsa, usa a menor tarifa
    # disponível para a mesma rota e data, evitando células vazias.
    return mapa.get(("rota_data", data_txt, origem, destino))


def flex(d, n):
    return [d + timedelta(days=i) for i in range(-n, n+1)]


# Regras de tabela fixa cadastradas no aplicativo.
# Só são exibidas quando origem/destino correspondem à regra.
ARGENTINA_AIRPORTS = {
    "EZE","AEP","COR","MDZ","BRC","IGR","USH","SLA","TUC","ROS","NQN","FTE"
}
BRAZIL_AIRPORTS = {
    "GYN","BSB","GRU","CGH","VCP","GIG","SDU","CNF","PLU","POA","CWB","REC",
    "SSA","FOR","BEL","MAO","FLN","NAT","MCZ","VIX","CGB","CGR","AJU","THE",
    "SLZ","JPA","NVT","LDB","UDI","IGU"
}

def fixed_table_rule(origens, destinos):
    o = set(origens)
    d = set(destinos)

    brasil_argentina = (
        (bool(o & BRAZIL_AIRPORTS) and bool(d & ARGENTINA_AIRPORTS))
        or
        (bool(o & ARGENTINA_AIRPORTS) and bool(d & BRAZIL_AIRPORTS))
    )

    if brasil_argentina:
        return {
            "programa": "LATAM Pass",
            "rota": "Brasil ↔ Argentina",
            "tabelas_cabine": {
                "Econômica": {
                    "milhas_por_trecho": 24000,
                    "url": "https://latampass.latam.com/pt_br/viagem/usar-milhas-para-voar/regras-de-resgate/latam/classe-economica"
                },
                "Premium Economy": {
                    "milhas_por_trecho": 43200,
                    "url": "https://latampass.latam.com/pt_br/viagem/usar-milhas-para-voar/regras-de-resgate/latam/classe-premium-economy"
                },
                "Executiva": {
                    "milhas_por_trecho": 48000,
                    "url": "https://latampass.latam.com/pt_br/viagem/usar-milhas-para-voar/regras-de-resgate/latam/classe-executiva"
                },
                "Primeira Classe": {
                    "milhas_por_trecho": 84000,
                    "url": "https://latampass.latam.com/pt_br/viagem/usar-milhas-para-voar/regras-de-resgate/latam/primeira-classe"
                }
            },
            "tipo_verificacao": "whatsapp",
            "contato_label": "📱 Verificar disponibilidade no WhatsApp LATAM",
            "contato_base": "https://api.whatsapp.com/send/?app_absent=0&phone=56968250850&text={mensagem}&type=phone_number",
            "regras_label": "📋 Regras gerais da tabela fixa",
            "regras_url": "https://latampass.latam.com/pt_br/viagem/usar-milhas-para-voar/regras-de-resgate/latam",
            "orientacao": (
                "Peça ao atendimento para confirmar especificamente disponibilidade "
                "de assento em companhia parceira pela tabela fixa LATAM Pass."
            ),
            "observacao": (
                "A tabela fixa depende da cabine, da rota e de disponibilidade em companhia parceira. "
                "Ela não se aplica a voos operados pela própria LATAM."
            )
        }

    # Novas tabelas fixas de outros programas devem ser cadastradas aqui
    # com programa, rota, milhas, contato/site oficial e URL das regras.
    return None


def extrair_historico_preco(dados, dias=30):
    insights = dados.get("price_insights") or {}
    historico = insights.get("price_history") or []

    if not historico:
        return pd.DataFrame(), insights

    agora = datetime.now()
    limite = agora - timedelta(days=dias)
    linhas = []

    for item in historico:
        try:
            ts, preco = item[0], item[1]
            dt = datetime.fromtimestamp(int(ts))
            if dt >= limite:
                linhas.append({
                    "Data": dt.date(),
                    "Preço (R$)": float(preco)
                })
        except Exception:
            continue

    if not linhas:
        return pd.DataFrame(), insights

    df = pd.DataFrame(linhas)
    df = df.sort_values("Data").drop_duplicates(subset=["Data"], keep="last")
    return df, insights

def classificar_preco_atual(preco_atual, historico_df, insights):
    if not historico_df.empty:
        valores = historico_df["Preço (R$)"].dropna().tolist()
        if valores:
            media = statistics.mean(valores)
            mediana = statistics.median(valores)
            minimo = min(valores)
            maximo = max(valores)
            diferenca_pct = ((preco_atual - media) / media * 100) if media else 0

            if preco_atual <= minimo * 1.05:
                nivel = "🟢 Bom preço"
            elif preco_atual < media * 0.90:
                nivel = "🟢 Bom preço"
            elif preco_atual <= media * 1.10:
                nivel = "🟡 Preço normal"
            else:
                nivel = "🔴 Preço alto"

            return {
                "nivel": nivel,
                "media": media,
                "mediana": mediana,
                "minimo": minimo,
                "maximo": maximo,
                "diferenca_pct": diferenca_pct
            }

    nivel_google = (insights or {}).get("price_level")
    mapa = {
        "low": "🟢 Bom preço",
        "typical": "🟡 Preço normal",
        "high": "🔴 Preço alto"
    }
    return {
        "nivel": mapa.get(nivel_google, "Sem histórico suficiente"),
        "media": None,
        "mediana": None,
        "minimo": None,
        "maximo": None,
        "diferenca_pct": None
    }


def exigir_senha():
    senha_configurada = _secret_or_env("APP_PASSWORD")
    if not senha_configurada:
        st.error(
            "A senha do aplicativo ainda não foi configurada. "
            "No Streamlit Cloud, adicione APP_PASSWORD em Settings > Secrets."
        )
        st.stop()

    if st.session_state.get("autenticado"):
        return

    # Tela de acesso exclusiva, sem sidebar e sem elementos técnicos.
    st.html("""
    <style>
    [data-testid="stSidebar"] {display:none !important;}
    [data-testid="stSidebarCollapsedControl"] {display:none !important;}
    header[data-testid="stHeader"] {display:none !important;}
    [data-testid="stToolbar"] {display:none !important;}
    [data-testid="stMainMenu"] {display:none !important;}
    [data-testid="stMainBlockContainer"] {
        max-width:100% !important;
        padding-top:3vh !important;
        padding-bottom:2rem !important;
    }
    .ftt-login-title{
        text-align:center;
        color:#08224A;
        font-size:2rem;
        line-height:1.1;
        font-weight:800;
        letter-spacing:-.03em;
        margin:.2rem 0 .4rem 0;
    }
    .ftt-login-sub{
        text-align:center;
        color:#6B7A90;
        font-size:1rem;
        margin-bottom:.2rem;
    }
    .ftt-login-secure{
        text-align:center;
        color:#8A98AA;
        font-size:.82rem;
        margin-top:.15rem;
        margin-bottom:1.2rem;
    }
    div[data-testid="stForm"]{
        background:#FFFFFF;
        border:1px solid #DFE7F0;
        border-radius:20px;
        padding:1.35rem 1.45rem 1.1rem 1.45rem;
        box-shadow:0 14px 40px rgba(8,34,74,.08);
    }
    div[data-testid="stForm"] button{
        width:100% !important;
        background:#087CF0 !important;
        color:#FFFFFF !important;
        border:1px solid #087CF0 !important;
        min-height:46px !important;
        border-radius:12px !important;
        font-weight:700 !important;
    }
    div[data-testid="stForm"] button:hover{
        background:#0668CC !important;
        border-color:#0668CC !important;
        color:#FFFFFF !important;
    }
    </style>
    """)

    _, centro, _ = st.columns([1, 1.05, 1])

    with centro:
        logo_login = BASE / "assets" / "frederico_travel_tools_logo.png"
        if not logo_login.exists():
            logo_login = BASE / "assets" / "marca_sidebar_aprovada.png"

        if logo_login.exists():
            st.image(str(logo_login), width="stretch")

        with st.form("login_ftt", clear_on_submit=False):
            senha_digitada = st.text_input(
                "Senha",
                type="password",
                placeholder="Digite sua senha"
            )
            entrar = st.form_submit_button("Entrar")

        if entrar:
            if hmac.compare_digest(senha_digitada, senha_configurada):
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta. Tente novamente.")

    st.stop()

exigir_senha()


def carregar_historico_local():
    if HIST_FILE.exists():
        try:
            df = pd.read_csv(HIST_FILE)
            if not df.empty and "capturado_em" in df.columns:
                df["capturado_em"] = pd.to_datetime(df["capturado_em"], errors="coerce")
            return df
        except Exception:
            pass
    return pd.DataFrame(columns=[
        "capturado_em","origens","destinos","ida","volta","adultos","cabine","conexoes","preco"
    ])

def salvar_ponto_historico(origens, destinos, ida, volta, adultos, cabine, conexoes, preco):
    if not preco:
        return
    df = carregar_historico_local()
    novo = pd.DataFrame([{
        "capturado_em": datetime.now().isoformat(timespec="seconds"),
        "origens": ",".join(origens),
        "destinos": ",".join(destinos),
        "ida": ida.isoformat(),
        "volta": volta.isoformat() if volta else "",
        "adultos": int(adultos) if adultos else "-",
        "cabine": str(cabine),
        "conexoes": str(conexoes),
        "preco": float(preco),
    }])
    df = pd.concat([df, novo], ignore_index=True)
    # Mantém somente os últimos 180 dias de registros
    df["capturado_em"] = pd.to_datetime(df["capturado_em"], errors="coerce")
    limite = pd.Timestamp.now() - pd.Timedelta(days=180)
    df = df[df["capturado_em"] >= limite]
    try:
        df.to_csv(HIST_FILE, index=False)
    except Exception:
        pass

def historico_proprio(origens, destinos, ida, volta, adultos, cabine, conexoes, dias):
    df = carregar_historico_local()
    if df.empty:
        return df
    try:
        limite = pd.Timestamp.now() - pd.Timedelta(days=dias)
        alvo_o = ",".join(origens)
        alvo_d = ",".join(destinos)
        mask = (
            (df["capturado_em"] >= limite) &
            (df["origens"] == alvo_o) &
            (df["destinos"] == alvo_d) &
            (df["ida"] == ida.isoformat()) &
            (df["volta"].fillna("") == (volta.isoformat() if volta else "")) &
            (df["adultos"].astype(int) == int(adultos)) &
            (df["cabine"].astype(str) == str(cabine)) &
            (df["conexoes"].astype(str) == str(conexoes))
        )
        return df.loc[mask, ["capturado_em","preco"]].sort_values("capturado_em")
    except Exception:
        return pd.DataFrame()


def grafico_historico_google_style(df, preco_atual=None):
    if df is None or df.empty:
        return

    g = df.copy()
    if "Data" not in g.columns:
        g = g.reset_index()
        if "index" in g.columns:
            g = g.rename(columns={"index": "Data"})
    g["Data"] = pd.to_datetime(g["Data"], errors="coerce")
    g["Preço (R$)"] = pd.to_numeric(g["Preço (R$)"], errors="coerce")
    g = g.dropna(subset=["Data", "Preço (R$)"]).sort_values("Data")
    if g.empty:
        return

    hoje = pd.Timestamp.now().normalize()
    def rel(d):
        dias = int((hoje - pd.Timestamp(d).normalize()).days)
        if dias <= 0:
            return "Hoje"
        if dias == 1:
            return "Há 1 dia"
        return f"Há {dias} dias"

    g["Quando"] = g["Data"].apply(rel)
    base = alt.Chart(g).encode(
        x=alt.X("Data:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=0, grid=False)),
        y=alt.Y("Preço (R$):Q", title=None, scale=alt.Scale(zero=False),
                axis=alt.Axis(format=",.0f", grid=True)),
        tooltip=[
            alt.Tooltip("Quando:N", title="Quando"),
            alt.Tooltip("Data:T", title="Data/hora", format="%d/%m/%Y %H:%M"),
            alt.Tooltip("Preço (R$):Q", title="Preço", format=",.2f")
        ]
    )
    area = base.mark_area(opacity=0.12)
    linha = base.mark_line(strokeWidth=3)
    pontos = base.mark_circle(size=45)

    chart = area + linha + pontos

    if preco_atual and preco_atual > 0:
        ref = pd.DataFrame({"Preço atual": [float(preco_atual)]})
        regra = alt.Chart(ref).mark_rule(strokeDash=[6,4]).encode(
            y=alt.Y("Preço atual:Q"),
            tooltip=[alt.Tooltip("Preço atual:Q", title="Preço atual", format=",.2f")]
        )
        chart = chart + regra

    st.altair_chart(
        chart.properties(height=280).configure_view(strokeWidth=0),
        width="stretch"
    )

if not api_key():
    st.error("Serviço de pesquisa indisponível. Verifique a configuração do aplicativo.")



# Base mundial de aeroportos IATA. Se o pacote não carregar, mantém alguns
# aeroportos essenciais como fallback para o app continuar funcionando.
def carregar_aeroportos():
    registros = []
    if airportsdata is not None:
        try:
            dados = airportsdata.load("IATA")
            for codigo, info in dados.items():
                cidade = (info.get("city") or "").strip()
                nome = (info.get("name") or "").strip()
                pais = (info.get("country") or "").strip()
                if codigo and (cidade or nome):
                    registros.append({
                        "codigo": codigo.upper(),
                        "cidade": cidade,
                        "nome": nome,
                        "pais": pais,
                    })
        except Exception:
            registros = []

    if not registros:
        fallback = [
            ("GYN","Goiânia","Santa Genoveva Airport","BR"),
            ("BSB","Brasília","Presidente Juscelino Kubitschek International Airport","BR"),
            ("GRU","São Paulo","São Paulo/Guarulhos International Airport","BR"),
            ("CGH","São Paulo","Congonhas Airport","BR"),
            ("VCP","Campinas","Viracopos International Airport","BR"),
            ("GIG","Rio de Janeiro","Rio de Janeiro/Galeão International Airport","BR"),
            ("SDU","Rio de Janeiro","Santos Dumont Airport","BR"),
            ("EZE","Buenos Aires","Ministro Pistarini International Airport","AR"),
            ("AEP","Buenos Aires","Aeroparque Jorge Newbery","AR"),
            ("LIM","Lima","Jorge Chávez International Airport","PE"),
            ("SCL","Santiago","Arturo Merino Benítez International Airport","CL"),
            ("BOG","Bogotá","El Dorado International Airport","CO"),
            ("MVD","Montevideo","Carrasco International Airport","UY"),
            ("ASU","Asunción","Silvio Pettirossi International Airport","PY"),
        ]
        registros = [
            {"codigo": c, "cidade": ci, "nome": n, "pais": p}
            for c, ci, n, p in fallback
        ]
    return registros

AEROPORTOS_MUNDO = carregar_aeroportos()

# Ajustes de área metropolitana para a experiência de busca.
# Ex.: VCP fica em Campinas, mas é frequentemente considerado uma opção para São Paulo.
ALIASES_CIDADE = {
    "GRU": ["São Paulo"],
    "CGH": ["São Paulo"],
    "VCP": ["São Paulo", "Campinas"],
    "GIG": ["Rio de Janeiro"],
    "SDU": ["Rio de Janeiro"],
    "EZE": ["Buenos Aires"],
    "AEP": ["Buenos Aires"],
}

def buscar_aeroportos_inteligente(termo, limite=12):
    termo = (termo or "").strip()
    if not termo:
        return []

    q = termo.casefold()
    exatos = []
    cidade_inicio = []
    outros = []

    for a in AEROPORTOS_MUNDO:
        codigo = a["codigo"]
        cidade = a["cidade"]
        nome = a["nome"]
        pais = a["pais"]

        if codigo.casefold() == q:
            exatos.append(a)
            continue

        cidade_cf = cidade.casefold()
        texto = f"{codigo} {cidade} {nome} {pais}".casefold()

        if cidade_cf == q or cidade_cf.startswith(q):
            cidade_inicio.append(a)
        elif q in texto:
            outros.append(a)

    # Prioriza código exato, depois cidade e só então outras correspondências.
    resultado = exatos + cidade_inicio + outros

    # Remove duplicados preservando a ordem.
    vistos = set()
    final = []
    for a in resultado:
        if a["codigo"] not in vistos:
            final.append(a)
            vistos.add(a["codigo"])
        if len(final) >= limite:
            break
    return final

def rotulo_aeroporto(a):
    cidade = a["cidade"] or "Cidade não informada"
    codigo = a["codigo"]
    pais = f" · {a['pais']}" if a.get("pais") else ""
    aliases = [x for x in ALIASES_CIDADE.get(codigo, []) if x.casefold() != cidade.casefold()]
    alias_txt = f" · atende {', '.join(aliases)}" if aliases else ""
    return f"{cidade} — {codigo}{pais}{alias_txt}"


def agrupar_resultados_aeroportos(resultados):
    grupos = {}
    for a in resultados:
        cidade = a.get("cidade") or "Outros"
        grupos.setdefault(cidade, []).append(a)
    return grupos


def campo_aeroporto_inteligente(titulo, key_prefix):
    """
    Permite vários aeroportos. Após a escolha, o menu fecha; para incluir
    outro, o usuário abre explicitamente "Adicionar outro aeroporto".
    """
    prioridade = {
        "GRU": 1, "CGH": 2, "VCP": 3,
        "GIG": 4, "SDU": 5,
        "EZE": 6, "AEP": 7,
        "GYN": 8, "BSB": 9, "LIM": 10
    }
    opcoes = sorted(
        AEROPORTOS_MUNDO,
        key=lambda a: (prioridade.get(a["codigo"], 9999), a["cidade"], a["codigo"])
    )

    por_codigo = {a["codigo"]: a for a in opcoes}

    lista_key = f"{key_prefix}_selecionados"
    input_key = f"{key_prefix}_novo_aeroporto"

    if lista_key not in st.session_state:
        st.session_state[lista_key] = []

    def _normalizar_escolha(valor):
        if not valor:
            return None
        if isinstance(valor, dict):
            codigo = valor.get("codigo")
            return por_codigo.get(codigo, valor)
        if isinstance(valor, str):
            # O Streamlit pode devolver o código/string serializada no callback.
            codigo = valor.strip().upper()
            if codigo in por_codigo:
                return por_codigo[codigo]
            # Tenta extrair um código IATA de 3 letras do texto formatado.
            for cod, aeroporto in por_codigo.items():
                if f"— {cod}" in valor or valor.endswith(cod) or f" {cod} " in valor:
                    return aeroporto
        return None

    def _adicionar_aeroporto():
        escolhido = _normalizar_escolha(st.session_state.get(input_key))
        if escolhido:
            cod = escolhido.get("codigo")
            atuais = st.session_state.get(lista_key, [])
            if cod and all(a.get("codigo") != cod for a in atuais):
                st.session_state[lista_key] = atuais + [escolhido]
        # Não força None no callback; isso evita conflito com o estado interno do widget.

    def _remover_aeroporto(codigo):
        st.session_state[lista_key] = [
            a for a in st.session_state.get(lista_key, [])
            if a.get("codigo") != codigo
        ]

    selecionados = st.session_state.get(lista_key, [])

    st.markdown(f"**{titulo}**")

    if not selecionados:
        escolha = st.selectbox(
            f"Selecionar {titulo.lower()}",
            options=opcoes,
            index=None,
            format_func=rotulo_aeroporto,
            key=input_key,
            placeholder="Digite a cidade ou aeroporto",
            label_visibility="collapsed",
            help=(
                "Digite o nome da cidade ou, se souber, a sigla do aeroporto. "
                "Ex.: Rio de Janeiro, GIG, São Paulo, CGH, Lima, LIM."
            )
        )

        escolha_norm = _normalizar_escolha(escolha)
        if escolha_norm:
            cod = escolha_norm.get("codigo")
            if cod and all(a.get("codigo") != cod for a in st.session_state.get(lista_key, [])):
                st.session_state[lista_key] = st.session_state.get(lista_key, []) + [escolha_norm]
                st.rerun()

    else:
        for a in selecionados:
            cidade = a.get("cidade") or ""
            codigo = a.get("codigo") or ""
            c1, c2 = st.columns([0.82, 0.18], gap="small")
            with c1:
                st.markdown(
                    f"<div style='padding:8px 10px;border:1px solid #dfe7f0;"
                    f"border-radius:10px;background:#fff;'>{cidade} — <b>{codigo}</b></div>",
                    unsafe_allow_html=True
                )
            with c2:
                st.button(
                    "✕",
                    key=f"{key_prefix}_remover_{codigo}",
                    help=f"Remover {codigo}",
                    on_click=_remover_aeroporto,
                    args=(codigo,),
                    width="stretch"
                )

        with st.popover("＋ Adicionar outro aeroporto", width="stretch"):
            restantes = [
                a for a in opcoes
                if all(s.get("codigo") != a.get("codigo") for s in selecionados)
            ]
            escolha_extra = st.selectbox(
                "Novo aeroporto",
                options=restantes,
                index=None,
                format_func=rotulo_aeroporto,
                key=f"{input_key}_extra_{len(selecionados)}",
                placeholder="Digite a cidade ou aeroporto",
                help="Escolha outro aeroporto para incluir na mesma origem ou destino."
            )

            escolha_extra_norm = _normalizar_escolha(escolha_extra)
            if escolha_extra_norm:
                cod = escolha_extra_norm.get("codigo")
                if cod and all(a.get("codigo") != cod for a in st.session_state.get(lista_key, [])):
                    st.session_state[lista_key] = st.session_state.get(lista_key, []) + [escolha_extra_norm]
                    st.rerun()

    atuais = st.session_state.get(lista_key, [])
    if not atuais:
        return ""

    return ",".join(a["codigo"] for a in atuais)


def _ftt_b64(p):
    try: return base64.b64encode(Path(p).read_bytes()).decode()
    except Exception: return ""

st.html("""
<style>
:root{--navy:#08224a;--blue:#0b84f3;--bg:#f7f9fc;--line:#e5ebf3}
.stApp{background:var(--bg)}
.block-container{padding-top:2.2rem;max-width:1480px}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}
h1,h2,h3{color:var(--navy);letter-spacing:-.025em}
div[data-testid="stMetric"]{background:#fff;border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:0 5px 18px rgba(8,34,74,.04)}
.stButton>button{border-radius:12px;min-height:44px;font-weight:700}
.stButton>button[kind="primary"]{
    background:#087CF0!important;
    color:#FFFFFF!important;
    border:1px solid #087CF0!important;
}
.stButton>button[kind="primary"]:hover{
    background:#0668CC!important;
    color:#FFFFFF!important;
    border-color:#0668CC!important;
}
.stButton>button:disabled{
    background:#E8EEF6!important;
    color:#8A98AA!important;
    border-color:#D8E1EC!important;
    opacity:1!important;
}
div[data-baseweb="input"],div[data-baseweb="select"]{border-radius:12px}
div[data-testid="stDownloadButton"]>button{
    background:#087CF0!important;color:#fff!important;border:1px solid #087CF0!important;
    border-radius:12px!important;font-weight:700!important;min-height:44px!important;
}
div[data-testid="stDownloadButton"]>button:hover{
    background:#0668CC!important;color:#fff!important;border-color:#0668CC!important;
}
.fttHero{background:#fff;border:1px solid var(--line);border-radius:20px;padding:18px 22px;margin:4px 0 22px;box-shadow:0 8px 28px rgba(8,34,74,.05);overflow:visible}
.fttHero img{display:block;width:min(560px,92%);height:auto;margin:auto;object-fit:contain}
.fttFooter{margin-top:38px;padding:20px 4px 8px;border-top:1px solid var(--line);text-align:center;color:#718096;font-size:.88rem}
</style>
""")
_logo=_ftt_b64(BASE/"assets"/"frederico_travel_tools_logo.png")
def _pdf_safe(v):
    if v is None:
        return "-"
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _pdf_money(v):
    try:
        return brl(float(v))
    except Exception:
        return "-"

def _pdf_p(v, style):
    return Paragraph(_pdf_safe(v), style)

def _pdf_chart_history(df, preco_atual=None):
    if df is None or df.empty:
        return None
    g = df.copy()
    if "Data" not in g.columns:
        g = g.reset_index()
        if "index" in g.columns:
            g = g.rename(columns={"index": "Data"})
    if "Preço (R$)" not in g.columns:
        return None
    g["Data"] = pd.to_datetime(g["Data"], errors="coerce")
    g["Preço (R$)"] = pd.to_numeric(g["Preço (R$)"], errors="coerce")
    g = g.dropna(subset=["Data", "Preço (R$)"]).sort_values("Data")
    if g.empty:
        return None

    fig, ax = plt.subplots(figsize=(9.5, 3.5))
    ax.plot(g["Data"], g["Preço (R$)"], linewidth=2.4, marker="o", markersize=4)
    ax.fill_between(g["Data"], g["Preço (R$)"], g["Preço (R$)"].min()*0.98, alpha=.10)
    if preco_atual:
        ax.axhline(float(preco_atual), linestyle="--", linewidth=1.4, label="Preço atual")
    ax.set_ylabel("Preço (R$)")
    ax.grid(axis="y", alpha=.20)
    ax.spines[["top","right"]].set_visible(False)

    locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    ax.tick_params(axis="x", labelsize=8)
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf

def gerar_relatorio_pdf(contexto):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=9*mm, leftMargin=9*mm,
        topMargin=8*mm, bottomMargin=9*mm,
        title="Frederico Travel Tools - Relatório de Pesquisa"
    )

    styles = getSampleStyleSheet()
    navy = colors.HexColor("#08224A")
    blue = colors.HexColor("#0B84F3")
    light = colors.HexColor("#F4F7FB")
    line = colors.HexColor("#DFE7F0")
    gray = colors.HexColor("#617087")

    h1 = ParagraphStyle("FTTH1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                        fontSize=17, leading=20, textColor=navy, spaceAfter=4)
    h2 = ParagraphStyle("FTTH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=12, leading=14, textColor=navy, spaceBefore=5, spaceAfter=4)
    body = ParagraphStyle("FTTBody", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=7.8, leading=10, textColor=colors.HexColor("#26364A"))
    small = ParagraphStyle("FTTSmall", parent=body, fontSize=6.8, leading=8, textColor=gray)
    center = ParagraphStyle("FTTCenter", parent=body, alignment=TA_CENTER)
    story = []
    secao = [0]

    def titulo_secao(titulo):
        secao[0] += 1
        return f"{secao[0]}. {titulo}"


    logo_file = BASE / "assets" / "frederico_travel_tools_logo.png"
    if logo_file.exists():
        story.append(RLImage(str(logo_file), width=78*mm, height=23*mm))
        story.append(Spacer(1, 2*mm))

    story.append(Paragraph("Relatório completo da pesquisa de passagens", h1))
    horario_brasilia = datetime.now(ZoneInfo("America/Sao_Paulo"))
    story.append(Paragraph(
        f"Gerado em {horario_brasilia.strftime('%d/%m/%Y às %H:%M')} - horário de Brasília",
        small
    ))
    story.append(Spacer(1, 4*mm))

    # Resumo da pesquisa
    story.append(Paragraph(titulo_secao("Resumo da pesquisa"), h2))
    dados = [
        ["Origem", contexto.get("origem","-"), "Destino", contexto.get("destino","-")],
        ["Tipo", contexto.get("tipo","-"), "Cabine", contexto.get("cabine","-")],
        ["Ida", contexto.get("ida","-"), "Volta", contexto.get("volta","-")],
        ["Adultos", str(contexto.get("adultos","-")), "Conexões", contexto.get("conexoes","-")],
    ]
    t = Table(dados, colWidths=[22*mm, 68*mm, 22*mm, 68*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.white),
        ("BOX",(0,0),(-1,-1),.5,line),
        ("INNERGRID",(0,0),(-1,-1),.35,line),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
        ("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
        ("TEXTCOLOR",(0,0),(0,-1),navy),("TEXTCOLOR",(2,0),(2,-1),navy),
        ("FONTSIZE",(0,0),(-1,-1),8.5),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.append(t)
    story.append(Spacer(1, 4*mm))

    # Resultados de voos
    voos = contexto.get("voos") or []
    story.append(Paragraph(titulo_secao("Resultados em dinheiro"), h2))
    if voos:
        menor = min(float(x.get("Preço (R$)", 0) or 0) for x in voos)
        story.append(Paragraph(f"Menor preço encontrado: <b>{_pdf_money(menor)}</b>", body))
        story.append(Spacer(1, 2*mm))
        cols = ["Preço (R$)","Companhia(s)","Origem","Destino","Saída ida","Chegada ida","Escalas","Duração ida","Voos"]
        mapa_pdf_ida = contexto.get("mapa_so_ida") or {}
        header = [
            _pdf_p("Data ida", small), _pdf_p("Orig.", small), _pdf_p("Dest.", small),
            _pdf_p("Companhia", small), _pdf_p("Esc.", small), _pdf_p("Duração", small),
            _pdf_p("Saída", small), _pdf_p("Chegada", small), _pdf_p("Voo(s)", small),
            _pdf_p("Só ida", small), _pdf_p("Total ida+volta", small)
        ]
        rows_pdf = [header]
        for x in voos[:12]:
            preco_so_ida_pdf = preco_so_trecho(
                mapa_pdf_ida, x.get("Ida"), x.get("Origem"), x.get("Destino"), x.get("Voos"),
                x.get("Companhia(s)"), x.get("Saída ida"), x.get("Duração ida")
            )
            rows_pdf.append([
                _pdf_p(x.get("Ida"), small),
                _pdf_p(x.get("Origem"), small),
                _pdf_p(x.get("Destino"), small),
                _pdf_p(x.get("Companhia(s)"), small),
                _pdf_p(x.get("Escalas"), small),
                _pdf_p(x.get("Duração ida"), small),
                _pdf_p(x.get("Saída ida"), small),
                _pdf_p(x.get("Chegada ida"), small),
                _pdf_p(x.get("Voos"), small),
                _pdf_p(_pdf_money(preco_so_ida_pdf) if preco_so_ida_pdf is not None else "—", small),
                _pdf_p(_pdf_money(x.get("Preço (R$)")), small),
            ])
        tab = Table(
            rows_pdf, repeatRows=1,
            colWidths=[16*mm,9*mm,9*mm,19*mm,8*mm,16*mm,22*mm,22*mm,22*mm,16*mm,19*mm],
            hAlign="LEFT"
        )
        tab.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),5.8),
            ("GRID",(0,0),(-1,-1),.3,line),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, light]),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(tab)

        retornos_pdf = contexto.get("retornos")
        if retornos_pdf is not None and isinstance(retornos_pdf, pd.DataFrame) and not retornos_pdf.empty:
            story.append(Spacer(1, 4*mm))
            story.append(Paragraph("Opções de volta", h2))

            datas_ret_pdf = []
            for d_txt in retornos_pdf["Data volta"].dropna().unique():
                try:
                    datas_ret_pdf.append(datetime.strptime(str(d_txt), "%d/%m/%Y").date())
                except Exception:
                    pass
            mapa_pdf_volta = mapa_precos_so_trecho(
                contexto.get("dest_codigos", []),
                contexto.get("orig_codigos", []),
                datas_ret_pdf,
                contexto.get("adultos", 1),
                contexto.get("cabine_codigo", 1),
                contexto.get("stops_codigo", 0)
            )

            ret_header = [
                _pdf_p("Data volta", small), _pdf_p("Orig.", small), _pdf_p("Dest.", small),
                _pdf_p("Companhia", small), _pdf_p("Esc.", small), _pdf_p("Duração", small),
                _pdf_p("Saída", small), _pdf_p("Chegada", small), _pdf_p("Voo(s)", small),
                _pdf_p("Só volta", small), _pdf_p("Total ida+volta", small)
            ]
            ret_rows = [ret_header]
            for _, r in retornos_pdf.drop(columns=["_data_principal"], errors="ignore").head(12).iterrows():
                preco_so_volta_pdf = preco_so_trecho(
                    mapa_pdf_volta, r.get("Data volta"), r.get("Origem"),
                    r.get("Destino"), r.get("Voos"),
                    r.get("Companhia(s)"), r.get("Saída"), r.get("Duração")
                )
                ret_rows.append([
                    _pdf_p(r.get("Data volta"), small),
                    _pdf_p(r.get("Origem"), small),
                    _pdf_p(r.get("Destino"), small),
                    _pdf_p(r.get("Companhia(s)"), small),
                    _pdf_p(r.get("Escalas"), small),
                    _pdf_p(r.get("Duração"), small),
                    _pdf_p(r.get("Saída"), small),
                    _pdf_p(r.get("Chegada"), small),
                    _pdf_p(r.get("Voos"), small),
                    _pdf_p(_pdf_money(preco_so_volta_pdf) if preco_so_volta_pdf is not None else "—", small),
                    _pdf_p(_pdf_money(r.get("Preço total (R$)")), small),
                ])

            ret_tab = Table(
                ret_rows, repeatRows=1,
                colWidths=[16*mm,9*mm,9*mm,19*mm,8*mm,16*mm,22*mm,22*mm,22*mm,16*mm,19*mm],
                hAlign="LEFT"
            )
            ret_tab.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),navy),
                ("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("GRID",(0,0),(-1,-1),.3,line),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, light]),
                ("FONTSIZE",(0,0),(-1,-1),5.5),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("LEFTPADDING",(0,0),(-1,-1),2),
                ("RIGHTPADDING",(0,0),(-1,-1),2),
                ("TOPPADDING",(0,0),(-1,-1),3),
                ("BOTTOMPADDING",(0,0),(-1,-1),3),
            ]))
            story.append(ret_tab)
    else:
        story.append(Paragraph("Nenhuma pesquisa de voo foi executada nesta sessão.", body))

    ida_sel = contexto.get("ida_escolhida")
    volta_sel = contexto.get("volta_escolhida")
    if ida_sel or volta_sel:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph("Voos selecionados", h2))

        if ida_sel:
            ida_rows = [
                [_pdf_p(x, small) for x in ["Trecho","Data","Origem","Destino","Companhia","Saída","Chegada","Duração","Voo(s)"]],
                [
                    _pdf_p("Ida", small),
                    _pdf_p(ida_sel.get("Ida"), small),
                    _pdf_p(ida_sel.get("Origem"), small),
                    _pdf_p(ida_sel.get("Destino"), small),
                    _pdf_p(ida_sel.get("Companhia(s)"), small),
                    _pdf_p(ida_sel.get("Saída ida"), small),
                    _pdf_p(ida_sel.get("Chegada ida"), small),
                    _pdf_p(ida_sel.get("Duração ida"), small),
                    _pdf_p(ida_sel.get("Voos"), small),
                ]
            ]
            ida_tab = Table(
                ida_rows,
                colWidths=[11*mm,18*mm,11*mm,11*mm,24*mm,27*mm,27*mm,18*mm,25*mm],
                hAlign="LEFT"
            )
            ida_tab.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("GRID",(0,0),(-1,-1),.3,line),
                ("FONTSIZE",(0,0),(-1,-1),5.8),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ]))
            story.append(ida_tab)
            story.append(Spacer(1, 3*mm))

        if volta_sel:
            volta_rows = [
                [_pdf_p(x, small) for x in ["Trecho","Data","Origem","Destino","Companhia","Saída","Chegada","Duração","Voo(s)"]],
                [
                    _pdf_p("Volta", small),
                    _pdf_p(volta_sel.get("Data volta"), small),
                    _pdf_p(volta_sel.get("Origem"), small),
                    _pdf_p(volta_sel.get("Destino"), small),
                    _pdf_p(volta_sel.get("Companhia(s)"), small),
                    _pdf_p(volta_sel.get("Saída"), small),
                    _pdf_p(volta_sel.get("Chegada"), small),
                    _pdf_p(volta_sel.get("Duração"), small),
                    _pdf_p(volta_sel.get("Voos"), small),
                ]
            ]
            volta_tab = Table(
                volta_rows,
                colWidths=[11*mm,18*mm,11*mm,11*mm,24*mm,27*mm,27*mm,18*mm,25*mm],
                hAlign="LEFT"
            )
            volta_tab.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("GRID",(0,0),(-1,-1),.3,line),
                ("FONTSIZE",(0,0),(-1,-1),5.8),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ]))
            story.append(volta_tab)

    # Histórico
    story.append(Paragraph(titulo_secao("Histórico de preços"), h2))
    hist = contexto.get("historico")
    if hist is not None and not hist.empty:
        chart = _pdf_chart_history(hist, contexto.get("preco_atual"))
        if chart:
            story.append(RLImage(chart, width=180*mm, height=62*mm))
        vals = pd.to_numeric(hist["Preço (R$)"], errors="coerce").dropna()
        if not vals.empty:
            metrics = [
                ["Preço atual", _pdf_money(contexto.get("preco_atual")),
                 "Média", _pdf_money(vals.mean()),
                 "Menor", _pdf_money(vals.min()),
                 "Maior", _pdf_money(vals.max())]
            ]
            mt = Table(metrics, colWidths=[21*mm,24*mm]*4)
            mt.setStyle(TableStyle([
                ("BOX",(0,0),(-1,-1),.5,line),("INNERGRID",(0,0),(-1,-1),.3,line),
                ("BACKGROUND",(0,0),(-1,-1),colors.white),
                ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
                ("FONTNAME",(0,0),(0,0),"Helvetica-Bold"),
                ("FONTNAME",(2,0),(2,0),"Helvetica-Bold"),
                ("FONTNAME",(4,0),(4,0),"Helvetica-Bold"),
                ("FONTNAME",(6,0),(6,0),"Helvetica-Bold"),
                ("TEXTCOLOR",(0,0),(-1,-1),navy),
                ("FONTSIZE",(0,0),(-1,-1),8),
                ("ALIGN",(1,0),(-1,-1),"CENTER"),
                ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ]))
            story.append(mt)
    else:
        story.append(Paragraph("Ainda não há histórico suficiente para esta pesquisa.", body))

    # Milhas e comparador - só entra no PDF quando o usuário informou que possui programa.
    if contexto.get("usar_milhas_pdf"):
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(titulo_secao("Saldos e comparação com milhas"), h2))
        story.append(Paragraph(
            "Dados de resgate informados pelo usuário após consulta ao programa ou a uma ferramenta externa. "
            "Disponibilidade e taxas devem ser confirmadas antes da emissão.",
            small
        ))
        story.append(Spacer(1, 2*mm))
        saldos = contexto.get("saldos", {})
        saldo_tbl = Table([
            ["LATAM Pass","Smiles","Azul Fidelidade"],
            [pts(saldos.get("LATAM Pass",0)), pts(saldos.get("Smiles",0)), pts(saldos.get("Azul Fidelidade",0))]
        ], colWidths=[60*mm]*3)
        saldo_tbl.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("ALIGN",(0,0),(-1,-1),"CENTER"),
            ("BOX",(0,0),(-1,-1),.5,line),("INNERGRID",(0,0),(-1,-1),.3,line),
            ("FONTSIZE",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)
        ]))
        story.append(saldo_tbl)
        story.append(Spacer(1, 3*mm))

        comp = contexto.get("ranking_milhas")
        if comp is not None and not comp.empty:
            headers = ["Posição","Opção","Você paga","Milhas usadas","Milhas faltam","Saldo depois"]
            rows_comp = [[_pdf_p(h, small) for h in headers]]
            for _, r in comp.iterrows():
                rows_comp.append([
                    _pdf_p(r.get("Posição"), small),
                    _pdf_p(r.get("Opção"), small),
                    _pdf_p(_pdf_money(r.get("Desembolso imediato")), small),
                    _pdf_p("—" if not r.get("Milhas exigidas") else pts(r.get("Milhas exigidas")), small),
                    _pdf_p("—" if not r.get("Milhas exigidas") else pts(r.get("Milhas faltantes")), small),
                    _pdf_p("—" if not r.get("Milhas exigidas") else pts(r.get("Saldo após emissão")), small),
                ])
            widths = [16*mm,38*mm,30*mm,31*mm,31*mm,31*mm]
            ct = Table(rows_comp, repeatRows=1, colWidths=widths, hAlign="LEFT")
            ct.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
                ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
                ("GRID",(0,0),(-1,-1),.3,line),
                ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, light]),
                ("FONTSIZE",(0,0),(-1,-1),6.2),
                ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
                ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ]))
            story.append(ct)

    # Tabela fixa
    fixa = contexto.get("tabela_fixa")
    if fixa and contexto.get("usar_milhas_pdf"):
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph(titulo_secao("Tabela fixa aplicável"), h2))
        fixa_rows = [
            [_pdf_p("Programa", body), _pdf_p(fixa.get("programa","-"), body),
             _pdf_p("Rota", body), _pdf_p(fixa.get("rota","-"), body)],
            [_pdf_p("Cabine", body), _pdf_p(fixa.get("cabine","-"), body),
             _pdf_p("Milhas por trecho", body), _pdf_p(pts(fixa.get("milhas_trecho",0)), body)],
            [_pdf_p("Total estimado", body), _pdf_p(pts(fixa.get("total",0)), body),
             _pdf_p("Milhas faltantes", body), _pdf_p(pts(fixa.get("faltantes",0)), body)],
            [_pdf_p("Preço máximo do milheiro", body),
             _pdf_p(_pdf_money(fixa.get("max_milheiro",0)) + " / 1.000", body),
             _pdf_p("Disponibilidade", body),
             _pdf_p(fixa.get("disponibilidade","-"), body)],
        ]
        ft = Table(
            fixa_rows,
            colWidths=[43*mm,47*mm,43*mm,47*mm],
            hAlign="LEFT"
        )
        ft.setStyle(TableStyle([
            ("BOX",(0,0),(-1,-1),.5,line),("INNERGRID",(0,0),(-1,-1),.3,line),
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white, light]),
            ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
            ("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
            ("TEXTCOLOR",(0,0),(0,-1),navy),("TEXTCOLOR",(2,0),(2,-1),navy),
            ("FONTSIZE",(0,0),(-1,-1),7.2),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ]))
        story.append(ft)

    # Recommendation
    rec = contexto.get("recomendacao")
    if rec:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph(titulo_secao("Recomendação"), h2))
        story.append(Table([[Paragraph(_pdf_safe(rec), body)]], colWidths=[180*mm],
                           style=TableStyle([
                               ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#EAF7EE")),
                               ("BOX",(0,0),(-1,-1),.6,colors.HexColor("#B9DFC4")),
                               ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
                               ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
                           ])))

    story.append(Spacer(1, 7*mm))
    story.append(Paragraph(
        "Preços, disponibilidade, regras dos programas de fidelidade e taxas devem ser confirmados antes da compra ou emissão. "
        "Frederico Travel Tools - uso informativo e comparativo.",
        small
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()




st.html("""
<style>
/* Remove completamente a faixa superior reservada pelo Streamlit. */
html, body, [data-testid="stAppViewContainer"], .stApp {
    margin-top:0 !important;
    padding-top:0 !important;
}
header[data-testid="stHeader"],
[data-testid="stHeader"],
[data-testid="stDecoration"] {
    display:none !important;
    height:0 !important;
    min-height:0 !important;
    max-height:0 !important;
}

/* Conteúdo principal começa no topo real da página. */
[data-testid="stMain"],
main[data-testid="stMain"],
section[data-testid="stMain"] {
    margin-top:0 !important;
    padding-top:0 !important;
    top:0 !important;
}
[data-testid="stMainBlockContainer"],
.main .block-container,
section.main > div.block-container {
    padding-top:0.25rem !important;
    margin-top:0 !important;
}

/* Barra lateral também começa no topo real. */
section[data-testid="stSidebar"],
[data-testid="stSidebar"] {
    top:0 !important;
    margin-top:0 !important;
    padding-top:0 !important;
    height:100vh !important;
}
[data-testid="stSidebarContent"],
[data-testid="stSidebar"] > div:first-child,
[data-testid="stSidebar"] .block-container {
    margin-top:0 !important;
    padding-top:0.25rem !important;
}

/* Remove espaços automáticos antes do primeiro elemento. */
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    gap:0.45rem !important;
}
[data-testid="stMain"] > div:first-child,
[data-testid="stSidebarContent"] > div:first-child {
    margin-top:0 !important;
    padding-top:0 !important;
}
</style>
""")


st.html("""
<style>
/* Remove a faixa reservada no topo da barra lateral. */
[data-testid="stSidebarHeader"] {
    display:none !important;
    height:0 !important;
    min-height:0 !important;
    max-height:0 !important;
    padding:0 !important;
    margin:0 !important;
}
[data-testid="stSidebarContent"] {
    padding-top:0 !important;
    margin-top:0 !important;
}
[data-testid="stSidebarContent"] > div {
    padding-top:0 !important;
    margin-top:0 !important;
}
[data-testid="stSidebar"] [data-testid="stImage"] {
    margin-top:0 !important;
    padding-top:0 !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    padding-top:0 !important;
    margin-top:0 !important;
}
</style>
""")

if "_pesquisa_versao" not in st.session_state:
    st.session_state["_pesquisa_versao"] = 0

with st.sidebar:
    marca_sidebar = BASE / "assets" / "marca_sidebar_aprovada.png"
    if marca_sidebar.exists():
        st.image(str(marca_sidebar), width="stretch")
    def _limpar_nova_pesquisa():
        # Cria um novo conjunto de widgets. Isso garante que os campos
        # reapareçam realmente vazios, sem reutilizar valores do navegador.
        st.session_state["_pesquisa_versao"] = int(
            st.session_state.get("_pesquisa_versao", 0)
        ) + 1

        # Limpa somente resultados e escolhas da consulta anterior.
        for _k in [
            "rank", "uso", "retornos", "preco_ref", "ultima_pesquisa",
            "price_insights_raw", "_ultimo_ponto_salvo", "retorno_sel_key",
            "ida_escolhida", "volta_escolhida", "periodo_historico",
            "tem_programas_milhas"
        ]:
            st.session_state.pop(_k, None)

        # Remove dados temporários de simulação.
        for _k in list(st.session_state.keys()):
            if (
                _k.startswith("req_v15_")
                or _k.startswith("tax_v15_")
                or _k.startswith("buy_v15_")
                or _k.startswith("fixed_buy_")
                or _k.startswith("cabine_fixa_")
                or _k.startswith("tabela_voos_")
            ):
                st.session_state.pop(_k, None)

    st.button(
        "Nova pesquisa",
        width="stretch",
        key="nova_pesquisa_btn",
        on_click=_limpar_nova_pesquisa
    )

    vpesq = int(st.session_state.get("_pesquisa_versao", 0))

    orig_txt = campo_aeroporto_inteligente(
        "Origem",
        f"origem_{vpesq}"
    )

    dest_txt = campo_aeroporto_inteligente(
        "Destino",
        f"destino_{vpesq}"
    )

    tipo_viagem = st.radio(
        "Tipo de viagem",
        ["Só ida", "Ida e volta"],
        horizontal=True,
        index=None,
        key=f"tipo_viagem_{vpesq}"
    )

    ida0 = st.date_input(
        "Data da ida",
        value=None,
        min_value=date.today(),
        format="DD/MM/YYYY",
        key=f"data_ida_{vpesq}"
    )

    if tipo_viagem == "Ida e volta":
        volta_sugerida = (ida0 + timedelta(days=1)) if ida0 else None
        if ida0 and st.session_state.get(f"_ida_usada_na_volta_{vpesq}") != ida0:
            st.session_state[f"data_volta_{vpesq}"] = volta_sugerida
            st.session_state[f"_ida_usada_na_volta_{vpesq}"] = ida0

        volta0 = st.date_input(
            "Data da volta",
            value=st.session_state.get(f"data_volta_{vpesq}", volta_sugerida),
            min_value=ida0 if ida0 else date.today(),
            format="DD/MM/YYYY",
            key=f"data_volta_{vpesq}"
        )
    else:
        volta0 = None

    fi = st.selectbox("Flexibilidade da ida", [0,1,2,3,5,7], index=None, placeholder="Selecione", key=f"flex_ida_{vpesq}")
    if tipo_viagem == "Ida e volta":
        fv = st.selectbox("Flexibilidade da volta", [0,1,2,3,5,7], index=None, placeholder="Selecione", key=f"flex_volta_{vpesq}")
    else:
        fv = 0
    adultos = st.selectbox(
        "Adultos",
        list(range(1, 10)),
        index=0,
        key=f"adultos_{vpesq}"
    )

    if True:
        cab_pt = st.selectbox(
            "Cabine",
            ["Econômica", "Premium Economy", "Executiva", "Primeira"],
            index=None,
            placeholder="Selecione",
            key=f"cabine_{vpesq}"
        )
        cab = {
            "Econômica": 1,
            "Premium Economy": 2,
            "Executiva": 3,
            "Primeira": 4
        }.get(cab_pt)

        stop_pt = st.selectbox(
            "Conexões",
            ["Qualquer quantidade", "Somente direto", "Até 1 conexão", "Até 2 conexões"],
            index=None,
            placeholder="Selecione",
            key=f"conexoes_{vpesq}"
        )
        stops = {
            "Qualquer quantidade": 0,
            "Somente direto": 1,
            "Até 1 conexão": 2,
            "Até 2 conexões": 3
        }.get(stop_pt)

orig = codigos(orig_txt)
dest = codigos(dest_txt)

campos_prontos = bool(
    orig and dest and tipo_viagem and ida0 and adultos
    and cab_pt and stop_pt
)
if not campos_prontos:
    for _k in [
        "rank", "uso", "retornos", "preco_ref", "ultima_pesquisa",
        "price_insights_raw", "retorno_sel_key", "ida_escolhida", "volta_escolhida"
    ]:
        st.session_state.pop(_k, None)

comb = []
if ida0 and fi is not None and adultos and cab is not None and stops is not None:
    if tipo_viagem == "Ida e volta":
        if volta0 and fv is not None:
            comb = [(i, v) for i in flex(ida0, fi) for v in flex(volta0, fv) if v > i]
    elif tipo_viagem == "Só ida":
        comb = [(i, None) for i in flex(ida0, fi)]





st.markdown('<div id="buscador"></div>', unsafe_allow_html=True)
hero_aprovado = BASE / "assets" / "hero_layout_aprovado.png"
if hero_aprovado.exists():
    st.image(str(hero_aprovado), width="stretch")
else:
    st.markdown("## Buscador Inteligente de Passagens")
    st.caption("Pesquise voos, acompanhe preços e compare dinheiro com milhas.")
st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

st.subheader("1. Pesquisa de passagens em dinheiro")
pode_pesquisar = bool(orig and dest and comb and tipo_viagem and adultos and cab_pt and stop_pt)

if not pode_pesquisar:
    st.caption("Preencha os dados da viagem no menu lateral para pesquisar.")

if st.button(
    "Pesquisar passagens",
    type="primary",
    disabled=not pode_pesquisar,
    width="stretch"
):
    for _k in ["retornos", "retorno_sel_key", "ida_escolhida", "volta_escolhida"]:
        st.session_state.pop(_k, None)
    rows = []
    novas = reap = 0
    prog = st.progress(0)
    erros_reais = []

    def _buscar_combinacao(par):
        ida, volta = par
        p = params_base(orig, dest, ida, volta, int(adultos), cab, stops)
        try:
            d, cached = consulta(p)
            return ida, volta, p, d, cached, None
        except Exception as exc:
            return ida, volta, p, None, False, exc

    max_workers = min(6, max(1, len(comb)))
    concluidas = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futuros = [executor.submit(_buscar_combinacao, par) for par in comb]

        for futuro in as_completed(futuros):
            ida, volta, p, d, cached, erro = futuro.result()
            concluidas += 1
            prog.progress(concluidas / len(comb))

            if erro is not None:
                msg = str(erro)
                # Ausência de voos em uma combinação de datas não é erro para o usuário.
                if "hasn't returned any results" not in msg.lower() and "no results" not in msg.lower():
                    erros_reais.append(msg)
                continue

            reap += int(cached)
            novas += int(not cached)

            if isinstance(d, dict) and d.get("price_insights"):
                st.session_state["price_insights_raw"] = d.get("price_insights") or {}

            for item in all_items(d):
                s = summarize(item)
                if s and isinstance(s["preco"], (int,float)) and (volta is None or s["token"]):
                    rows.append({
                        "Ida": data_br(ida),
                        "Volta": data_br(volta) if volta else "—",
                        "Preço (R$)": float(s["preco"]),
                        "Origem": s["origem"],
                        "Destino": s["destino"],
                        "Companhia(s)": s["cias"],
                        "Escalas": s["escalas"],
                        "Duração ida": s["duracao"],
                        "Saída ida": data_br(s["saida"]),
                        "Chegada ida": data_br(s["chegada"]),
                        "Voos": s["voos"],
                        "_token": s["token"],
                        "_params": p
                    })

    prog.empty()

    if not rows:
        if erros_reais:
            st.error("Não foi possível concluir a pesquisa. Tente novamente em alguns instantes.")
        else:
            st.info("Não foram encontrados voos para os filtros e datas informados.")

    rows = sorted(rows, key=lambda x:(x["Preço (R$)"], x["Escalas"]))
    st.session_state["rank"] = rows
    st.session_state["uso"] = (novas, reap)
    st.session_state["ultima_pesquisa"] = {
        "orig": list(orig),
        "dest": list(dest),
        "ida": ida0,
        "volta": volta0,
        "adultos": int(adultos) if adultos else "-",
        "cabine": cab_pt,
    "cabine_codigo": cab,
    "stops_codigo": stops,
        "conexoes": stop_pt,
    }

rank = st.session_state.get("rank", [])
if rank:
    novas, reap = st.session_state.get("uso", (0,0))
    st.success(f"Foram encontradas **{len(rank)} opções**.")

    menor = rank[0]["Preço (R$)"]
    st.session_state["preco_ref"] = menor
    chave_hist = (
        tuple(orig), tuple(dest), ida0, volta0, int(adultos), cab_pt, stop_pt, float(menor)
    )
    if st.session_state.get("_ultimo_ponto_salvo") != chave_hist:
        salvar_ponto_historico(orig, dest, ida0, volta0, adultos, cab_pt, stop_pt, menor)
        st.session_state["_ultimo_ponto_salvo"] = chave_hist
    st.metric("Menor preço encontrado", brl(menor))

    top = rank[:20]

    # Preço avulso de cada voo de ida. É uma pesquisa "somente ida",
    # não uma divisão do valor total da passagem ida e volta.
    mapa_so_ida = {}
    if volta0:
        datas_ida_exibidas = []
        for x in top:
            try:
                datas_ida_exibidas.append(datetime.strptime(x["Ida"], "%d/%m/%Y").date())
            except Exception:
                pass
        mapa_so_ida = mapa_precos_so_trecho(
            orig, dest, datas_ida_exibidas, adultos, cab, stops
        )

    linhas_ida_tabela = []
    for x in top:
        linha = {k: v for k, v in x.items() if not k.startswith("_")}
        if volta0:
            preco_avulso_ida = preco_so_trecho(
                mapa_so_ida, x.get("Ida"), x.get("Origem"),
                x.get("Destino"), x.get("Voos"),
                x.get("Companhia(s)"), x.get("Saída ida"), x.get("Duração ida")
            )
            linha["Só ida (R$)"] = preco_avulso_ida
            # O preço original do Google Flights em pesquisa ida e volta
            # representa o itinerário completo.
            linha["Total ida + volta (R$)"] = linha.pop("Preço (R$)")
        linhas_ida_tabela.append(linha)

    df_ida = pd.DataFrame(linhas_ida_tabela)

    if volta0:
        st.markdown("#### Opções de ida")
        st.caption(
            "“Só ida” é o preço avulso daquele voo pesquisado separadamente. "
            "“Total ida + volta” é o preço do itinerário completo."
        )

        evento_ida = st.dataframe(
            df_ida,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="tabela_voos_ida",
            column_config={
                "Só ida (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Total ida + volta (R$)": st.column_config.NumberColumn(format="R$ %.2f")
            }
        )

        linhas_ida = list(evento_ida.selection.rows) if evento_ida else []
        if linhas_ida:
            idx_ida = int(linhas_ida[0])
            st.session_state["ida_escolhida"] = top[idx_ida]

        # As opções de volta ficam visíveis sem exigir clique na ida.
        # Por padrão, priorizamos exatamente as datas principais escolhidas
        # pelo usuário, mesmo quando há flexibilidade de ± dias.
        if st.session_state.get("ida_escolhida") in top:
            sel_retorno = st.session_state.get("ida_escolhida")
        else:
            data_ida_principal = data_br(ida0)
            data_volta_principal = data_br(volta0)

            candidatos_exatos = [
                x for x in rank
                if x.get("Ida") == data_ida_principal
                and x.get("Volta") == data_volta_principal
            ]

            # Se por algum motivo não houver a combinação exata da ida,
            # ainda prioriza a data de volta escolhida.
            if candidatos_exatos:
                sel_retorno = sorted(
                    candidatos_exatos,
                    key=lambda x: (x["Preço (R$)"], x["Escalas"])
                )[0]
            else:
                candidatos_volta = [
                    x for x in rank
                    if x.get("Volta") == data_volta_principal
                ]
                sel_retorno = (
                    sorted(
                        candidatos_volta,
                        key=lambda x: (x["Preço (R$)"], x["Escalas"])
                    )[0]
                    if candidatos_volta
                    else top[0]
                )

        datas_volta_busca = [
            d for d in flex(volta0, fv)
            if d > datetime.strptime(sel_retorno["Ida"], "%d/%m/%Y").date()
        ]

        retorno_key = (
            f"{sel_retorno.get('_token','')}|{sel_retorno.get('Ida','')}|"
            f"{','.join(d.isoformat() for d in datas_volta_busca)}"
        )

        if st.session_state.get("retorno_sel_key") != retorno_key:
            rr = []

            def _buscar_retorno_data(data_retorno):
                p_ret = dict(sel_retorno["_params"])
                p_ret["departure_token"] = sel_retorno["_token"]
                p_ret["return_date"] = data_retorno.isoformat()
                try:
                    d_ret, _ = consulta(p_ret)
                    return data_retorno, d_ret, None
                except Exception as exc:
                    return data_retorno, None, exc

            with ThreadPoolExecutor(max_workers=min(4, max(1, len(datas_volta_busca)))) as executor:
                futuros_ret = [
                    executor.submit(_buscar_retorno_data, d)
                    for d in datas_volta_busca
                ]

                for futuro in as_completed(futuros_ret):
                    data_retorno, d_retorno, erro_retorno = futuro.result()
                    if erro_retorno is not None or not d_retorno:
                        continue

                    for item in all_items(d_retorno):
                        s = summarize(item)
                        if s:
                            rr.append({
                                "Preço total (R$)": s["preco"],
                                "Data volta": data_br(data_retorno),
                                "Origem": s["origem"],
                                "Destino": s["destino"],
                                "Companhia(s)": s["cias"],
                                "Escalas": s["escalas"],
                                "Duração": s["duracao"],
                                "Saída": data_br(s["saida"]),
                                "Chegada": data_br(s["chegada"]),
                                "Voos": s["voos"],
                                "_data_principal": data_retorno == volta0
                            })

            if rr:
                df_retorno = pd.DataFrame(rr)
                df_retorno["Preço total (R$)"] = pd.to_numeric(
                    df_retorno["Preço total (R$)"],
                    errors="coerce"
                )
                df_retorno = df_retorno.sort_values(
                    ["Preço total (R$)", "Data volta", "Saída"],
                    na_position="last"
                ).reset_index(drop=True)
                st.session_state["retornos"] = df_retorno
            else:
                st.session_state.pop("retornos", None)

            st.session_state["retorno_sel_key"] = retorno_key

        st.markdown("#### Opções de volta")
        if "retornos" in st.session_state and not st.session_state["retornos"].empty:
            if fv:
                datas_txt = ", ".join(data_br(d) for d in flex(volta0, fv))
                st.caption(
                    f"Datas pesquisadas para a volta: {datas_txt}. "
                    f"A data principal é {data_br(volta0)}. "
                    "“Só volta” é a tarifa avulsa pesquisada separadamente para a mesma data e rota."
                )
            else:
                st.caption(f"Data da volta: {data_br(volta0)}.")

            # Consulta o valor avulso (somente volta) para os voos exibidos.
            datas_retorno_exibidas = []
            for d_txt in st.session_state["retornos"]["Data volta"].dropna().unique():
                try:
                    datas_retorno_exibidas.append(datetime.strptime(str(d_txt), "%d/%m/%Y").date())
                except Exception:
                    pass

            mapa_so_volta = mapa_precos_so_trecho(
                dest, orig, datas_retorno_exibidas, adultos, cab, stops
            )

            df_volta_visivel = st.session_state["retornos"].drop(
                columns=["_data_principal"],
                errors="ignore"
            ).copy()

            df_volta_visivel["Só volta (R$)"] = df_volta_visivel.apply(
                lambda r: preco_so_trecho(
                    mapa_so_volta,
                    r.get("Data volta"),
                    r.get("Origem"),
                    r.get("Destino"),
                    r.get("Voos"),
                    r.get("Companhia(s)"), r.get("Saída"), r.get("Duração")
                ),
                axis=1
            )

            # Mantém o mesmo padrão visual da tabela de ida:
            # dados do voo primeiro e preços individuais/total nas duas últimas colunas.
            df_volta_visivel = df_volta_visivel.rename(
                columns={"Preço total (R$)": "Total ida + volta (R$)"}
            )
            ordem_volta = [
                "Data volta", "Origem", "Destino", "Companhia(s)", "Escalas",
                "Duração", "Saída", "Chegada", "Voos",
                "Só volta (R$)", "Total ida + volta (R$)"
            ]
            df_volta_visivel = df_volta_visivel[
                [c for c in ordem_volta if c in df_volta_visivel.columns]
            ]

            evento_volta = st.dataframe(
                df_volta_visivel,
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="tabela_voos_volta",
                column_config={
                    "Só volta (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                    "Total ida + volta (R$)": st.column_config.NumberColumn(format="R$ %.2f")
                }
            )

            linhas_volta = list(evento_volta.selection.rows) if evento_volta else []
            if linhas_volta:
                idx_volta = int(linhas_volta[0])
                volta_escolhida = st.session_state["retornos"].iloc[idx_volta].to_dict()
                st.session_state["volta_escolhida"] = volta_escolhida

                # Na resposta de seleção da volta, "Preço total (R$)" representa
                # o valor final do itinerário completo (ida + volta), e não
                # somente o trecho de retorno.
                total_selecionado = float(volta_escolhida.get("Preço total (R$)") or 0)
                if total_selecionado > 0:
                    st.session_state["preco_ref"] = total_selecionado

            ida_sel_atual = st.session_state.get("ida_escolhida")
            volta_sel_atual = st.session_state.get("volta_escolhida")
            if ida_sel_atual and volta_sel_atual:
                total_viagem = float(volta_sel_atual.get("Preço total (R$)") or 0)
                if total_viagem > 0:
                    st.success(
                        f"Total da viagem selecionada (ida + volta): **{brl(total_viagem)}**"
                    )
        else:
            st.caption("Não foram encontradas opções de volta para as datas pesquisadas.")
    else:
        st.dataframe(
            df_ida,
            width="stretch",
            hide_index=True,
            column_config={
                "Preço (R$)": st.column_config.NumberColumn(format="R$ %.2f")
            }
        )



if st.session_state.get("rank"):
    st.divider()
    st.markdown('<div id="historico"></div>', unsafe_allow_html=True)
    st.subheader("2. Histórico de preços")

    periodo_hist = st.radio(
        "Comparar o preço atual com:",
        ["Últimos 30 dias", "Últimos 60 dias"],
        horizontal=True,
        key="periodo_historico"
    )
    dias_hist = 30 if periodo_hist == "Últimos 30 dias" else 60
    st.caption("O gráfico é atualizado automaticamente ao trocar entre 30 e 60 dias, usando a última pesquisa realizada.")

    # O Streamlit atualiza esta seção automaticamente ao clicar em 30 ou 60 dias.
    # Não é necessário executar uma nova varredura.
    ultima = st.session_state.get("ultima_pesquisa", {})
    hist_orig = ultima.get("orig", orig)
    hist_dest = ultima.get("dest", dest)
    hist_ida = ultima.get("ida", ida0)
    hist_volta = ultima.get("volta", volta0)
    hist_adultos = ultima.get("adultos", int(adultos) if adultos else 1)
    hist_cabine = ultima.get("cabine", cab_pt)
    hist_conexoes = ultima.get("conexoes", stop_pt)

    preco_atual_hist = float(st.session_state.get("preco_ref", 0) or 0)
    insights_raw = st.session_state.get("price_insights_raw", {}) or {}

    hist_google = pd.DataFrame()
    if insights_raw:
        hist_fake_container = {"price_insights": insights_raw}
        hist_google, _ = extrair_historico_preco(hist_fake_container, dias_hist)

    hist_local = historico_proprio(
        hist_orig, hist_dest, hist_ida, hist_volta,
        hist_adultos, hist_cabine, hist_conexoes, dias_hist
    )

    if preco_atual_hist > 0 and not hist_google.empty:
        analise = classificar_preco_atual(preco_atual_hist, hist_google, insights_raw)
        st.caption("Histórico de preços atualizado.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preço atual", brl(preco_atual_hist))
        nivel_curto = analise["nivel"].replace("🟢 ", "").replace("🟡 ", "").replace("🔴 ", "")
        c2.metric("Avaliação", nivel_curto)
        c3.metric("Média do período", brl(analise["media"]))
        sentido_media = "acima" if analise["diferenca_pct"] >= 0 else "abaixo"
        c4.metric("Vs. média", f"{abs(analise['diferenca_pct']):.1f}% {sentido_media}")
        st.caption(f"Avaliação do preço: {analise['nivel']}")

        graf = hist_google.copy()
        graf["Data"] = pd.to_datetime(graf["Data"])
        graf = graf.set_index("Data")
        graf["Preço atual"] = preco_atual_hist
        grafico_historico_google_style(graf.reset_index(), preco_atual_hist)
        faixa_min = brl(analise["minimo"]).replace("$", r"\$")
        faixa_max = brl(analise["maximo"]).replace("$", r"\$")
        st.caption(f"Faixa de preços no período: menor {faixa_min} · maior {faixa_max}")

    elif preco_atual_hist > 0 and not hist_local.empty:
        st.caption("Histórico de preços atualizado.")
        h = hist_local.copy()
        h["capturado_em"] = pd.to_datetime(h["capturado_em"])
        h = h.rename(columns={"capturado_em":"Data", "preco":"Preço (R$)"}).set_index("Data")
        media = float(h["Preço (R$)"].mean())
        minimo = float(h["Preço (R$)"].min())
        maximo = float(h["Preço (R$)"].max())
        diferenca = ((preco_atual_hist-media)/media*100) if media else 0

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Preço atual", brl(preco_atual_hist))
        if diferenca < -10:
            avaliacao_local = "🟢 Bom preço"
        elif diferenca <= 10:
            avaliacao_local = "🟡 Preço normal"
        else:
            avaliacao_local = "🔴 Preço alto"
        avaliacao_curta = avaliacao_local.replace("🟢 ", "").replace("🟡 ", "").replace("🔴 ", "")
        c2.metric("Avaliação", avaliacao_curta)
        c3.metric("Média do período", brl(media))
        sentido_local = "acima" if diferenca >= 0 else "abaixo"
        c4.metric("Vs. média", f"{abs(diferenca):.1f}% {sentido_local}")
        st.caption(f"Avaliação do preço: {avaliacao_local}")

        h["Preço atual"] = preco_atual_hist
        grafico_historico_google_style(h.reset_index(), preco_atual_hist)
        faixa_min = brl(minimo).replace("$", r"\$")
        faixa_max = brl(maximo).replace("$", r"\$")
        st.caption(f"Faixa de preços no período: menor {faixa_min} · maior {faixa_max}")

    else:
        st.info(
            "Ainda não há histórico suficiente para esta pesquisa. "
            "A partir de agora, cada pesquisa concluída salva o menor preço encontrado para formar seu histórico."
        )


saved = load_saldos()

# Valores padrão para manter compatibilidade com relatório e cálculos posteriores.
lat = int(saved.get("LATAM Pass", 0))
smi = int(saved.get("Smiles", 0))
azu = int(saved.get("Azul Fidelidade", 0))
rows = []
status_programas = []
tem_milhas = "Não"
programas_escolhidos = []

if rank:
    st.divider()
    st.markdown('<div id="milhas"></div>', unsafe_allow_html=True)
    st.subheader("3. Milhas e programas de fidelidade")

    tem_milhas = st.radio(
        "Você participa de algum programa de milhas ou pontos?",
        ["Não", "Sim"],
        horizontal=True,
        index=None,
        key="tem_programas_milhas"
    )

if tem_milhas == "Sim":
    st.markdown("#### Consultar disponibilidade com pontos")
    st.caption(
        "Consulte a disponibilidade de emissões com pontos no Seats.aero e depois informe aqui "
        "somente os pontos e as taxas encontrados."
    )

    st.link_button(
        "Consultar no Seats.aero",
        "https://seats.aero/search",
        width="stretch"
    )

    if volta0:
        st.info(
            f"Pesquise: {', '.join(orig)} → {', '.join(dest)} em {data_br(ida0)} "
            f"e {', '.join(dest)} → {', '.join(orig)} em {data_br(volta0)}."
        )
    else:
        st.info(
            f"Pesquise: {', '.join(orig)} → {', '.join(dest)} em {data_br(ida0)}."
        )

    st.caption(
        "Se o Seats.aero não mostrar disponibilidade gratuita para sua rota ou data, "
        "continue normalmente e informe os dados encontrados diretamente no programa de milhas."
    )

    programas_escolhidos = st.multiselect(
        "Quais programas você utiliza?",
        ["Smiles", "LATAM Pass", "Azul Fidelidade"],
        default=[],
        placeholder="Selecione um ou mais programas",
        help="Escolha apenas os programas em que você possui conta ou saldo."
    )

    if not programas_escolhidos:
        st.caption("Selecione pelo menos um programa para continuar a comparação com milhas.")

    if programas_escolhidos:
        st.markdown("#### Seus saldos")
        st.caption("Informe somente o saldo atual dos programas selecionados.")

        saldo_cols = st.columns(len(programas_escolhidos))
        for idx, nome_programa in enumerate(programas_escolhidos):
            with saldo_cols[idx]:
                if nome_programa == "Smiles":
                    smi = st.number_input(
                        "Saldo Smiles",
                        min_value=0,
                        value=int(saved.get("Smiles", 0)),
                        step=1000,
                        key="saldo_smiles_v15"
                    )
                    st.metric("Smiles", pts(smi))
                elif nome_programa == "LATAM Pass":
                    lat = st.number_input(
                        "Saldo LATAM Pass",
                        min_value=0,
                        value=int(saved.get("LATAM Pass", 0)),
                        step=1000,
                        key="saldo_latam_v15"
                    )
                    st.metric("LATAM Pass", pts(lat))
                elif nome_programa == "Azul Fidelidade":
                    azu = st.number_input(
                        "Saldo Azul Fidelidade",
                        min_value=0,
                        value=int(saved.get("Azul Fidelidade", 0)),
                        step=1000,
                        key="saldo_azul_v15"
                    )
                    st.metric("Azul Fidelidade", pts(azu))

        if st.button("Salvar meus saldos"):
            save_saldos({
                "LATAM Pass": int(lat),
                "Smiles": int(smi),
                "Azul Fidelidade": int(azu)
            })
            st.success("Saldos atualizados.")

# -----------------------------------------------------------
# Comparador
# -----------------------------------------------------------
st.markdown('<div id="precos"></div>', unsafe_allow_html=True)

preco_ref = float(st.session_state.get("preco_ref", 0) or 0)

def _rota_eh_brasil(origens, destinos):
    return bool(set(origens) & BRAZIL_AIRPORTS) and bool(set(destinos) & BRAZIL_AIRPORTS)

def _rota_brasil_argentina(origens, destinos):
    o = set(origens)
    d = set(destinos)
    return (
        (bool(o & BRAZIL_AIRPORTS) and bool(d & ARGENTINA_AIRPORTS))
        or
        (bool(d & BRAZIL_AIRPORTS) and bool(o & ARGENTINA_AIRPORTS))
    )

def taxa_resgate_referencia(prefixo, origens, destinos, ida, adultos):
    dias = max((ida - date.today()).days, 0) if ida else 0
    adultos = max(int(adultos), 1)
    nacional = _rota_eh_brasil(origens, destinos)
    brasil_argentina = _rota_brasil_argentina(origens, destinos)

    if prefixo == "LATAM":
        if nacional:
            if dias >= 90:
                return 0.0, "Isenta pela antecedência informada."
            return 34.0 * adultos, "Referência automática para a emissão selecionada."
        if brasil_argentina:
            if dias >= 120:
                return 0.0, "Isenta pela antecedência informada."
            return 94.68 * adultos, "Referência automática para a emissão selecionada."
        if dias >= 120:
            return 0.0, "Isenta pela antecedência informada."
        return 220.92 * adultos, "Referência automática para a emissão selecionada."

    if prefixo == "AZUL":
        if dias >= 90:
            return 0.0, "Referência de isenção pela antecedência informada."
        if nacional:
            return 69.80 * adultos, "Referência automática para a emissão selecionada."
        return 237.80 * adultos, "Referência automática para a emissão selecionada."

    if prefixo == "SMILES":
        return 0.0, "A taxa pode variar conforme a emissão."

    return 0.0, ""

def _secret_float(nome, padrao):
    try:
        return float(_secret_or_env(nome, str(padrao)).replace(",", "."))
    except Exception:
        return float(padrao)



# Dados internos usados pelo ranking. A interface só pede o necessário.
if rank and tem_milhas == "Sim" and programas_escolhidos and preco_ref > 0:
    st.subheader("4. Comparar dinheiro × milhas")
    st.caption(
        f"Preço em dinheiro usado como referência: {brl(preco_ref)}. "
        "Informe apenas os pontos exigidos e as taxas do resgate."
    )

    mapa_programas = {
        "Smiles": (smi, "SMILES"),
        "LATAM Pass": (lat, "LATAM"),
        "Azul Fidelidade": (azu, "AZUL"),
    }

    for nome in programas_escolhidos:
        saldo, prefixo = mapa_programas[nome]

        with st.expander(f"Simular com {nome}", expanded=False):
            st.caption(
                "Informe os dados do resgate. Se o Seats.aero não mostrar as taxas, "
                "confirme o valor diretamente no programa antes da emissão."
            )
            req = st.number_input(
                "Quantas milhas/pontos o programa está cobrando?",
                min_value=0,
                value=0,
                step=1000,
                key=f"req_v15_{prefixo}"
            )

            taxa_auto, taxa_msg = taxa_resgate_referencia(
                prefixo, orig, dest, ida0, adultos
            )
            tax = st.number_input(
                "Taxas da emissão (R$)",
                min_value=0.0,
                value=float(taxa_auto),
                step=1.0,
                key=f"tax_v15_{prefixo}",
                help="Se o programa mostrar outro valor no checkout, substitua aqui."
            )
            if taxa_msg:
                st.caption(taxa_msg)

            falt = max(int(req) - int(saldo), 0)

            compra1000 = 0.0
            if falt > 0:
                st.warning(f"Seu saldo não é suficiente. Faltam {pts(falt)} milhas/pontos.")
                compra1000 = st.number_input(
                    "Se quiser completar o saldo, quanto custa comprar 1.000 milhas/pontos? (R$)",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    key=f"buy_v15_{prefixo}"
                )

            # Parâmetro interno para comparar o valor de milhas que já existem no saldo.
            valor1000 = _secret_float(f"{prefixo}_OWN_VALUE_PER_1000", 15.0)

            if req > 0:
                compra = (falt / 1000) * compra1000 if falt > 0 and compra1000 > 0 else 0.0
                pode_incluir = falt == 0 or compra1000 > 0

                if pode_incluir:
                    imed = float(tax) + float(compra)
                    econ = (min(int(req), int(saldo)) / 1000) * valor1000 + imed

                    rows.append({
                        "Opção": nome,
                        "Desembolso imediato": imed,
                        "Custo econômico": econ,
                        "Milhas exigidas": int(req),
                        "Milhas faltantes": int(falt),
                        "Saldo após emissão": max(int(saldo) - int(req), 0)
                    })

                    st.success(
                        f"Simulação pronta: você paga {brl(imed)} em dinheiro "
                        f"e usa {pts(int(req))} milhas/pontos."
                    )
                elif falt > 0:
                    st.caption("Informe o preço para completar as milhas se quiser incluir essa opção na comparação.")

# -----------------------------------------------------------
# Tabela fixa: só aparece quando a rota possui regra E o usuário selecionou o programa.
# -----------------------------------------------------------
regra_fixa = fixed_table_rule(orig, dest)
disponibilidade_fixa = "Não se aplica"
fixed_req_trecho = 0
fixed_req = 0
fixed_missing = 0
max_milheiro = 0
cabine_fixa = cab_pt

mostrar_fixa = (
    regra_fixa is not None
    and tem_milhas == "Sim"
    and regra_fixa.get("programa") in programas_escolhidos
)

if mostrar_fixa:
    st.markdown(f"### Tabela fixa disponível para esta rota — {regra_fixa['programa']}")
    st.caption(
        "Esta rota possui uma regra de tabela fixa cadastrada. "
        "A disponibilidade de assento precisa ser confirmada antes da emissão."
    )

    mensagem_verificacao = (
        f"Quero verificar disponibilidade para resgate com tabela fixa {regra_fixa['programa']}.\\n"
        f"Origem(ns): {', '.join(orig)}\\n"
        f"Destino(s): {', '.join(dest)}\\n"
        f"Data de ida: {data_br(ida0)}\\n"
        f"Data de volta: {data_br(volta0)}\\n"
        f"Cabine: {cab_pt}\\n"
        f"Adultos: {int(adultos)}\\n"
        "Gostaria de confirmar se há assento disponível pela tabela fixa e a quantidade final de milhas/pontos e taxas."
    )

    contato_url = regra_fixa.get("contato_base", "")
    if "{mensagem}" in contato_url:
        contato_url = contato_url.replace("{mensagem}", quote(mensagem_verificacao))

    b1, b2 = st.columns(2)
    with b1:
        if contato_url:
            st.link_button(
                "Verificar disponibilidade",
                contato_url,
                type="primary",
                width="stretch"
            )
    with b2:
        if regra_fixa.get("regras_url"):
            st.link_button(
                "Ver regras oficiais",
                regra_fixa["regras_url"],
                width="stretch"
            )

    disponibilidade_fixa = st.radio(
        "Depois de verificar, há assento disponível pela tabela fixa?",
        ["Ainda não verifiquei", "Não", "Sim"],
        horizontal=True
    )

    if disponibilidade_fixa == "Sim":
        cabines_disponiveis = list(regra_fixa.get("tabelas_cabine", {}).keys())
        cabine_fixa = st.selectbox(
            "Cabine",
            cabines_disponiveis,
            index=0,
            key="cabine_fixa_v15"
        )
        regra_cabine = regra_fixa["tabelas_cabine"][cabine_fixa]
        fixed_req_trecho = int(regra_cabine["milhas_por_trecho"])
        fixed_req = int(fixed_req_trecho * (2 if volta0 else 1))
        fixed_missing = max(fixed_req - int(lat), 0)

        c1, c2, c3 = st.columns(3)
        c1.metric("Milhas necessárias", pts(fixed_req))
        c2.metric("Seu saldo", pts(lat))
        c3.metric("Milhas faltantes", pts(fixed_missing))

        fixed_tax_auto, _ = taxa_resgate_referencia("LATAM", orig, dest, ida0, adultos)
        fixed_tax = float(fixed_tax_auto)

        fixed_buy = 0.0
        if fixed_missing > 0:
            fixed_buy = st.number_input(
                "Preço para comprar 1.000 milhas e completar o saldo (R$)",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key="fixed_buy_v15"
            )

        milhas_para_comprar = fixed_missing if fixed_missing > 0 else fixed_req
        max_milheiro = (
            max(preco_ref - fixed_tax, 0) / milhas_para_comprar * 1000
            if milhas_para_comprar else 0
        )
        st.caption(
            f"Para esta comparação, comprar milhas por até aproximadamente "
            f"{brl(max_milheiro)} por 1.000 ainda pode ser competitivo frente ao preço em dinheiro."
        )

        if fixed_missing == 0:
            rows.append({
                "Opção": f"{regra_fixa['programa']} tabela fixa",
                "Desembolso imediato": fixed_tax,
                "Custo econômico": fixed_tax + (fixed_req / 1000) * 15,
                "Milhas exigidas": fixed_req,
                "Milhas faltantes": 0,
                "Saldo após emissão": int(lat) - fixed_req
            })
        elif fixed_buy > 0:
            compra = fixed_missing / 1000 * fixed_buy
            rows.append({
                "Opção": f"{regra_fixa['programa']} tabela fixa",
                "Desembolso imediato": fixed_tax + compra,
                "Custo econômico": fixed_tax + compra + (min(int(lat), fixed_req) / 1000) * 15,
                "Milhas exigidas": fixed_req,
                "Milhas faltantes": fixed_missing,
                "Saldo após emissão": 0
            })

# -----------------------------------------------------------
# Ranking simplificado
# -----------------------------------------------------------
ranking = [{
    "Opção": "Dinheiro",
    "Desembolso imediato": preco_ref,
    "Custo econômico": preco_ref,
    "Milhas exigidas": 0,
    "Milhas faltantes": 0,
    "Saldo após emissão": 0
}] + rows

ranking = sorted(
    ranking,
    key=lambda x: (x["Custo econômico"], x["Desembolso imediato"])
)

for pos, x in enumerate(ranking, 1):
    x["Posição"] = pos

# DataFrame completo continua existindo para o PDF.
rdf = pd.DataFrame(ranking)[[
    "Posição",
    "Opção",
    "Desembolso imediato",
    "Custo econômico",
    "Milhas exigidas",
    "Milhas faltantes",
    "Saldo após emissão"
]]

if rank and preco_ref > 0 and (rows or tem_milhas in ["Não", "Sim"]):
    st.markdown("### Melhor forma de pagar")

    ranking_visual = []
    for x in ranking:
        ranking_visual.append({
            "Posição": x["Posição"],
            "Opção": x["Opção"],
            "Você paga": brl(x["Desembolso imediato"]),
            "Milhas usadas": "—" if x["Milhas exigidas"] == 0 else pts(x["Milhas exigidas"]),
            "Milhas que faltam": "—" if x["Milhas exigidas"] == 0 else pts(x["Milhas faltantes"]),
            "Saldo depois": "—" if x["Milhas exigidas"] == 0 else pts(x["Saldo após emissão"]),
        })

    st.dataframe(
        pd.DataFrame(ranking_visual),
        width="stretch",
        hide_index=True
    )

    melhor = ranking[0]
    st.markdown("### Recomendação")

    if melhor["Opção"] == "Dinheiro":
        st.success(f"Melhor opção: pagar em dinheiro — {brl(preco_ref)}.")
    else:
        economia_caixa = max(preco_ref - melhor["Desembolso imediato"], 0)
        milhas_usadas = int(melhor["Milhas exigidas"] or 0)
        valor_por_1000 = (
            economia_caixa / milhas_usadas * 1000
            if milhas_usadas > 0 else 0
        )

        # Referência econômica interna usada pelo ranking.
        if "Smiles" in melhor["Opção"]:
            referencia_1000 = _secret_float("SMILES_OWN_VALUE_PER_1000", 15.0)
        elif "LATAM" in melhor["Opção"]:
            referencia_1000 = _secret_float("LATAM_OWN_VALUE_PER_1000", 15.0)
        elif "Azul" in melhor["Opção"]:
            referencia_1000 = _secret_float("AZUL_OWN_VALUE_PER_1000", 15.0)
        else:
            referencia_1000 = 15.0

        valor_economico_milhas = milhas_usadas / 1000 * referencia_1000
        vantagem_economica = economia_caixa - valor_economico_milhas

        st.success(
            f"Melhor opção: {melhor['Opção']}. "
            f"Você paga {brl(melhor['Desembolso imediato'])} e usa "
            f"{pts(milhas_usadas)} milhas/pontos."
        )

        a1, a2, a3 = st.columns(3)
        a1.metric("Economia em dinheiro", brl(economia_caixa))
        a2.metric("Valor obtido por 1.000 milhas", brl(valor_por_1000))
        a3.metric(
            "Vantagem econômica estimada",
            brl(vantagem_economica)
        )

        if valor_por_1000 > referencia_1000:
            st.caption(
                f"Bom uso das milhas: neste resgate, cada 1.000 milhas geram cerca de "
                f"{brl(valor_por_1000)}, acima da referência de {brl(referencia_1000)} por 1.000. "
                f"Considerando o valor econômico das milhas usadas, a vantagem estimada é "
                f"{brl(vantagem_economica)}."
            )
        else:
            st.caption(
                f"Este resgate economiza dinheiro no momento, mas entrega cerca de "
                f"{brl(valor_por_1000)} por 1.000 milhas, abaixo da referência de "
                f"{brl(referencia_1000)} por 1.000. Pagar em dinheiro pode preservar melhor "
                f"o valor das suas milhas."
            )
elif preco_ref <= 0:
    rdf = pd.DataFrame(ranking)
st.divider()
if rank:
    st.caption(
        "Preços, disponibilidade e regras dos programas de fidelidade devem ser confirmados antes da compra ou emissão."
    )


# -----------------------------------------------------------
# Relatório completo em PDF
# -----------------------------------------------------------
hist_pdf = None
try:
    if "graf" in locals() and isinstance(graf, pd.DataFrame) and not graf.empty:
        hist_pdf = graf.reset_index() if "Data" not in graf.columns else graf.copy()
    elif "h" in locals() and isinstance(h, pd.DataFrame) and not h.empty:
        hist_pdf = h.reset_index() if "Data" not in h.columns else h.copy()
except Exception:
    hist_pdf = None

tabela_fixa_pdf = None
try:
    if regra_fixa:
        tabela_fixa_pdf = {
            "programa": regra_fixa.get("programa"),
            "rota": regra_fixa.get("rota"),
            "cabine": cabine_fixa if "cabine_fixa" in locals() else cab_pt,
            "milhas_trecho": fixed_req_trecho if "fixed_req_trecho" in locals() else 0,
            "total": fixed_req if "fixed_req" in locals() else 0,
            "faltantes": fixed_missing if "fixed_missing" in locals() else 0,
            "max_milheiro": max_milheiro if "max_milheiro" in locals() else 0,
            "disponibilidade": disponibilidade_fixa if "disponibilidade_fixa" in locals() else "Não verificada",
        }
except Exception:
    tabela_fixa_pdf = None

recomendacao_pdf = ""
try:
    if ranking:
        melhor = ranking[0]
        if melhor["Opção"] == "Dinheiro":
            recomendacao_pdf = (
                f"Dinheiro é a opção mais econômica pelos dados informados. "
                f"Desembolso estimado: {brl(melhor['Desembolso imediato'])}. "
                f"Custo econômico: {brl(melhor['Custo econômico'])}."
            )
        else:
            recomendacao_pdf = (
                f"{melhor['Opção']} é a melhor opção pelos dados informados. "
                f"Desembolso estimado: {brl(melhor['Desembolso imediato'])}. "
                f"Custo econômico: {brl(melhor['Custo econômico'])}."
            )
except Exception:
    pass

contexto_pdf = {
    "origem": ", ".join(orig) if orig else "-",
    "destino": ", ".join(dest) if dest else "-",
    "orig_codigos": list(orig),
    "dest_codigos": list(dest),
    "tipo": "Ida e volta" if volta0 else "Só ida",
    "cabine": cab_pt,
    "ida": data_br(ida0) if ida0 else "-",
    "volta": data_br(volta0) if volta0 else "-",
    "adultos": int(adultos) if adultos else "-",
    "conexoes": stop_pt,
    "voos": [{k:v for k,v in x.items() if not k.startswith("_")} for x in rank[:20]] if rank else [],
    "ida_escolhida": st.session_state.get("ida_escolhida"),
    "volta_escolhida": st.session_state.get("volta_escolhida"),
    "retornos": st.session_state.get("retornos"),
    "mapa_so_ida": mapa_so_ida if "mapa_so_ida" in locals() else {},
    "preco_atual": float(st.session_state.get("preco_ref",0) or 0),
    "historico": hist_pdf,
    "usar_milhas_pdf": tem_milhas == "Sim",
    "saldos": {"LATAM Pass": lat, "Smiles": smi, "Azul Fidelidade": azu} if tem_milhas == "Sim" else {},
    "ranking_milhas": (rdf if "rdf" in locals() else None) if tem_milhas == "Sim" else None,
    "tabela_fixa": tabela_fixa_pdf if tem_milhas == "Sim" else None,
    "recomendacao": recomendacao_pdf,
}

if rank:
    st.divider()
    st.markdown('<div id="relatorios"></div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="
        background:#fff;border:1px solid #dfe7f0;border-radius:18px;
        padding:18px 20px;margin-top:10px;box-shadow:0 5px 18px rgba(8,34,74,.04);
    ">
      <div style="font-size:1.05rem;font-weight:800;color:#08224a;">Relatório completo em PDF</div>
      <div style="color:#718096;margin-top:4px;">
        Baixe um relatório com voos, histórico de preços, comparação de milhas informadas e recomendação final.
      </div>
    </div>
    """, unsafe_allow_html=True)
    st.caption(
        "Baixe um PDF organizado com a marca Frederico Travel Tools, dados da viagem, "
        "resultados de voos, histórico de preços, gráfico, saldos, comparador de milhas, "
        "tabela fixa aplicável e recomendação."
    )

    if rank:
        try:
            pdf_bytes = gerar_relatorio_pdf(contexto_pdf)
            nome_pdf = f"Frederico_Travel_Tools_{'-'.join(orig) or 'origem'}_{'-'.join(dest) or 'destino'}_{ida0.strftime('%d-%m-%Y')}.pdf"
            st.download_button(
                "⬇️ Baixar relatório completo em PDF",
                data=pdf_bytes,
                file_name=nome_pdf,
                mime="application/pdf",
                type="primary",
                width="stretch"
            )
        except Exception as e:
            st.warning(f"Não foi possível gerar o PDF nesta sessão: {e}")
st.markdown("""
<div class="fttFooter">
Desenvolvido por Frederico Afonso Farias · © 2026 · Dados via SerpApi · Tenha uma excelente busca!
</div>
""",unsafe_allow_html=True)
