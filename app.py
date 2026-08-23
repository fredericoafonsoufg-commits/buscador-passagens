 
import os, json, hashlib, statistics, hmac
from urllib.parse import quote
from datetime import date, timedelta, datetime
from pathlib import Path

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
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image as RLImage, PageBreak, KeepTogether
)

st.set_page_config(
    page_title="Frederico Travel Tools",
    page_icon="🌐",
    layout="wide"
)

st.markdown("""
<style>
[data-testid="stToolbar"] {display:none !important;}
[data-testid="stDecoration"] {display:none !important;}
[data-testid="stStatusWidget"] {display:none !important;}
#MainMenu {visibility:hidden !important;}
</style>
""", unsafe_allow_html=True)

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

def consulta(params, force=False):
    f = CACHE_DIR / f"{cache_key(params)}.json"
    if f.exists() and not force:
        return json.loads(f.read_text(encoding="utf-8")), True

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
                nivel = "Muito baixo"
            elif preco_atual < media * 0.90:
                nivel = "Baixo"
            elif preco_atual <= media * 1.10:
                nivel = "Na média"
            else:
                nivel = "Alto"

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
        "low": "Baixo",
        "typical": "Na média",
        "high": "Alto"
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

    st.subheader("Buscador Inteligente de Passagens")
    st.caption("Acesso privado")

    senha_digitada = st.text_input("Senha", type="password")
    entrar = st.button("Entrar", type="primary")

    if entrar:
        if hmac.compare_digest(senha_digitada, senha_configurada):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Senha incorreta.")

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
        "adultos": int(adultos),
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

    escolhidos = st.multiselect(
        titulo,
        options=opcoes,
        default=[],
        format_func=rotulo_aeroporto,
        key=f"{key_prefix}_aeroportos",
        placeholder="Digite a cidade ou aeroporto",
        help=(
            "Digite o nome da cidade ou, se souber, a sigla do aeroporto. "
            "Ex.: Rio de Janeiro, GIG, São Paulo, CGH, Lima, LIM."
        )
    )

    if not escolhidos:
        return ""

    codigos = [a["codigo"] for a in escolhidos]
    return ",".join(codigos)


def _ftt_b64(p):
    try: return base64.b64encode(Path(p).read_bytes()).decode()
    except Exception: return ""

st.markdown("""
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
""",unsafe_allow_html=True)
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
        pagesize=landscape(A4),
        rightMargin=13*mm, leftMargin=13*mm,
        topMargin=12*mm, bottomMargin=13*mm,
        title="Frederico Travel Tools - Relatório de Pesquisa"
    )

    styles = getSampleStyleSheet()
    navy = colors.HexColor("#08224A")
    blue = colors.HexColor("#0B84F3")
    light = colors.HexColor("#F4F7FB")
    line = colors.HexColor("#DFE7F0")
    gray = colors.HexColor("#617087")

    h1 = ParagraphStyle("FTTH1", parent=styles["Heading1"], fontName="Helvetica-Bold",
                        fontSize=20, leading=24, textColor=navy, spaceAfter=6)
    h2 = ParagraphStyle("FTTH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=14, leading=18, textColor=navy, spaceBefore=8, spaceAfter=7)
    body = ParagraphStyle("FTTBody", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=8.6, leading=12, textColor=colors.HexColor("#26364A"))
    small = ParagraphStyle("FTTSmall", parent=body, fontSize=7.4, leading=9, textColor=gray)
    center = ParagraphStyle("FTTCenter", parent=body, alignment=TA_CENTER)
    story = []

    logo_file = BASE / "assets" / "frederico_travel_tools_logo.png"
    if logo_file.exists():
        story.append(RLImage(str(logo_file), width=105*mm, height=31*mm))
        story.append(Spacer(1, 2*mm))

    story.append(Paragraph("Relatório completo da pesquisa de passagens", h1))
    story.append(Paragraph(
        f"Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} · Desenvolvido por Frederico Afonso Farias",
        small
    ))
    story.append(Spacer(1, 4*mm))

    # Resumo da pesquisa
    story.append(Paragraph("1. Resumo da pesquisa", h2))
    dados = [
        ["Origem", contexto.get("origem","-"), "Destino", contexto.get("destino","-")],
        ["Tipo", contexto.get("tipo","-"), "Cabine", contexto.get("cabine","-")],
        ["Ida", contexto.get("ida","-"), "Volta", contexto.get("volta","-")],
        ["Adultos", str(contexto.get("adultos","-")), "Conexões", contexto.get("conexoes","-")],
    ]
    t = Table(dados, colWidths=[27*mm, 70*mm, 27*mm, 70*mm])
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
    story.append(Paragraph("2. Resultados em dinheiro", h2))
    if voos:
        menor = min(float(x.get("Preço (R$)", 0) or 0) for x in voos)
        story.append(Paragraph(f"Menor preço encontrado: <b>{_pdf_money(menor)}</b>", body))
        story.append(Spacer(1, 2*mm))
        cols = ["Preço (R$)","Companhia(s)","Origem","Destino","Saída ida","Chegada ida","Escalas","Duração ida","Voos"]
        header = ["Preço","Companhia","Orig.","Dest.","Saída","Chegada","Esc.","Duração","Voo(s)"]
        rows_pdf = [header]
        for x in voos[:20]:
            rows_pdf.append([
                _pdf_money(x.get("Preço (R$)")),
                _pdf_safe(x.get("Companhia(s)")),
                _pdf_safe(x.get("Origem")),
                _pdf_safe(x.get("Destino")),
                _pdf_safe(x.get("Saída ida")),
                _pdf_safe(x.get("Chegada ida")),
                _pdf_safe(x.get("Escalas")),
                _pdf_safe(x.get("Duração ida")),
                _pdf_safe(x.get("Voos")),
            ])
        tab = Table(rows_pdf, repeatRows=1, colWidths=[22*mm,42*mm,15*mm,15*mm,31*mm,31*mm,12*mm,22*mm,32*mm])
        tab.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTSIZE",(0,0),(-1,-1),7.1),
            ("GRID",(0,0),(-1,-1),.3,line),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, light]),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
        ]))
        story.append(tab)
    else:
        story.append(Paragraph("Nenhuma pesquisa de voo foi executada nesta sessão.", body))

    # Histórico
    story.append(PageBreak())
    story.append(Paragraph("3. Histórico de preços", h2))
    hist = contexto.get("historico")
    if hist is not None and not hist.empty:
        chart = _pdf_chart_history(hist, contexto.get("preco_atual"))
        if chart:
            story.append(RLImage(chart, width=230*mm, height=82*mm))
        vals = pd.to_numeric(hist["Preço (R$)"], errors="coerce").dropna()
        if not vals.empty:
            metrics = [
                ["Preço atual", _pdf_money(contexto.get("preco_atual")),
                 "Média", _pdf_money(vals.mean()),
                 "Menor", _pdf_money(vals.min()),
                 "Maior", _pdf_money(vals.max())]
            ]
            mt = Table(metrics, colWidths=[24*mm,30*mm]*4)
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

    # Milhas e comparador
    story.append(Spacer(1, 5*mm))
    story.append(Paragraph("4. Saldos e comparação com milhas", h2))
    saldos = contexto.get("saldos", {})
    saldo_tbl = Table([
        ["LATAM Pass","Smiles","Azul Fidelidade"],
        [pts(saldos.get("LATAM Pass",0)), pts(saldos.get("Smiles",0)), pts(saldos.get("Azul Fidelidade",0))]
    ], colWidths=[65*mm]*3)
    saldo_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("BOX",(0,0),(-1,-1),.5,line),("INNERGRID",(0,0),(-1,-1),.3,line),
        ("FONTSIZE",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7)
    ]))
    story.append(saldo_tbl)
    story.append(Spacer(1, 4*mm))

    comp = contexto.get("ranking_milhas")
    if comp is not None and not comp.empty:
        headers = list(comp.columns)
        rows_comp = [headers]
        for _, r in comp.iterrows():
            row = []
            for c in headers:
                v = r[c]
                if c in ("Desembolso imediato","Custo econômico"):
                    row.append(_pdf_money(v))
                elif "Milhas" in c or "Saldo" in c:
                    try: row.append(pts(v))
                    except: row.append(_pdf_safe(v))
                else:
                    row.append(_pdf_safe(v))
            rows_comp.append(row)
        widths = [16*mm,44*mm,34*mm,34*mm,29*mm,29*mm,29*mm][:len(headers)]
        ct = Table(rows_comp, repeatRows=1, colWidths=widths)
        ct.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),navy),("TEXTCOLOR",(0,0),(-1,0),colors.white),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("GRID",(0,0),(-1,-1),.3,line),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, light]),
            ("FONTSIZE",(0,0),(-1,-1),7.2),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
        ]))
        story.append(ct)

    # Tabela fixa
    fixa = contexto.get("tabela_fixa")
    if fixa:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph("5. Tabela fixa aplicável", h2))
        fixa_rows = [
            ["Programa", fixa.get("programa","-"), "Rota", fixa.get("rota","-")],
            ["Cabine", fixa.get("cabine","-"), "Milhas por trecho", pts(fixa.get("milhas_trecho",0))],
            ["Total estimado", pts(fixa.get("total",0)), "Milhas faltantes", pts(fixa.get("faltantes",0))],
            ["Preço máximo do milheiro", _pdf_money(fixa.get("max_milheiro",0)) + " / 1.000",
             "Disponibilidade", fixa.get("disponibilidade","-")],
        ]
        ft = Table(fixa_rows, colWidths=[38*mm,64*mm,48*mm,64*mm])
        ft.setStyle(TableStyle([
            ("BOX",(0,0),(-1,-1),.5,line),("INNERGRID",(0,0),(-1,-1),.3,line),
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[colors.white, light]),
            ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
            ("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
            ("TEXTCOLOR",(0,0),(0,-1),navy),("TEXTCOLOR",(2,0),(2,-1),navy),
            ("FONTSIZE",(0,0),(-1,-1),8.2),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
        ]))
        story.append(ft)

    # Recommendation
    rec = contexto.get("recomendacao")
    if rec:
        story.append(Spacer(1, 5*mm))
        story.append(Paragraph("6. Recomendação", h2))
        story.append(Table([[Paragraph(_pdf_safe(rec), body)]], colWidths=[230*mm],
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



# Top navigation / section shortcuts
st.markdown("""
<style>
.fttTopNav{
  display:flex;align-items:center;justify-content:space-between;gap:18px;
  background:#fff;border:1px solid #e5ebf3;border-radius:16px;
  padding:11px 16px;margin:0 0 18px 0;box-shadow:0 4px 16px rgba(8,34,74,.04);
}
.fttTopNav .brandMini{font-weight:850;color:#08224a;font-size:1.02rem;white-space:nowrap}
.fttTopNav .links{display:flex;gap:18px;align-items:center;flex-wrap:wrap;justify-content:center;flex:1}
.fttTopNav a{
  color:#17325d;text-decoration:none;font-weight:750;font-size:.93rem;
  padding:7px 10px;border-radius:10px;
}
.fttTopNav a:hover{color:#087CF0;background:#f2f7ff}
.fttTopRight{display:flex;align-items:center;gap:10px}
.fttBadge{
  background:#f2f8ff;color:#087CF0;border:1px solid #d8e7ff;
  border-radius:999px;padding:6px 10px;font-size:.8rem;font-weight:800;
}
.fttAvatar{
  width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:#087CF0;color:white;font-weight:800;font-size:.82rem;
}
@media(max-width:900px){
  .fttTopNav{align-items:flex-start;flex-direction:column}
  .fttTopNav .links{justify-content:flex-start}
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="fttTopNav">
  <div class="brandMini">Frederico Travel Tools</div>
  <div class="links">
    <a href="#buscador">Buscador</a>
    <a href="#precos">Preços</a>
    <a href="#milhas">Milhas</a>
    <a href="#historico">Histórico</a>
    <a href="#relatorios">Relatórios</a>
  </div>
  <div class="fttTopRight">
    <div class="fttBadge">V13.1 Web</div>
    <div class="fttAvatar">FA</div>
  </div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    if _logo:
        st.markdown(
            f"""
            <div style="padding:4px 8px 12px;text-align:center;">
              <img src="data:image/png;base64,{_logo}"
                   style="width:100%;max-width:240px;height:auto;object-fit:contain;">
            </div>
            """,
            unsafe_allow_html=True
        )
    st.markdown("### Nova pesquisa")
    st.caption("Origem, destino e datas em poucos passos.")

    orig_txt = campo_aeroporto_inteligente(
        "Origem",
        "origem"
    )

    dest_txt = campo_aeroporto_inteligente(
        "Destino",
        "destino"
    )

    if "data_ida" not in st.session_state:
        st.session_state["data_ida"] = date.today() + timedelta(days=30)
    if "data_volta" not in st.session_state:
        st.session_state["data_volta"] = date.today() + timedelta(days=37)

    tipo_viagem = st.radio(
        "Tipo de viagem",
        ["Só ida", "Ida e volta"],
        horizontal=True,
        key="tipo_viagem"
    )

    ida0 = st.date_input(
        "Data da ida",
        min_value=date.today(),
        format="DD/MM/YYYY",
        key="data_ida"
    )

    if tipo_viagem == "Ida e volta":
        if st.session_state["data_volta"] < ida0:
            st.session_state["data_volta"] = ida0 + timedelta(days=7)

        volta0 = st.date_input(
            "Data da volta",
            min_value=ida0,
            format="DD/MM/YYYY",
            key="data_volta"
        )
    else:
        volta0 = None

    fi = st.selectbox("Flexibilidade da ida", [0,1,2,3,5,7], index=0)
    if tipo_viagem == "Ida e volta":
        fv = st.selectbox("Flexibilidade da volta", [0,1,2,3,5,7], index=0)
    else:
        fv = 0
    adultos = st.number_input("Adultos", 1, 9, 1)

    with st.expander("Filtros avançados"):
        cab_pt = st.selectbox(
            "Cabine",
            ["Econômica", "Premium Economy", "Executiva", "Primeira"]
        )
        cab = {
            "Econômica": 1,
            "Premium Economy": 2,
            "Executiva": 3,
            "Primeira": 4
        }[cab_pt]

        stop_pt = st.selectbox(
            "Conexões",
            ["Qualquer quantidade", "Somente direto", "Até 1 conexão", "Até 2 conexões"]
        )
        stops = {
            "Qualquer quantidade": 0,
            "Somente direto": 1,
            "Até 1 conexão": 2,
            "Até 2 conexões": 3
        }[stop_pt]

orig = codigos(orig_txt)
dest = codigos(dest_txt)
if volta0:
    comb = [(i, v) for i in flex(ida0, fi) for v in flex(volta0, fv) if v > i]
else:
    comb = [(i, None) for i in flex(ida0, fi)]


st.markdown('<div id="buscador"></div>', unsafe_allow_html=True)
_hero_b64 = _ftt_b64(BASE / "assets" / "hero_aviao_moderno.png")
if _hero_b64:
    st.markdown(
        f"""
        <div class="fttHero2">
          <div class="inner">
            <div class="copy">
              <h1>Buscador Inteligente<br><span>de Passagens</span></h1>
              <p>Pesquise voos, acompanhe preços e compare dinheiro com milhas.</p>
              <div class="features">
                <div class="fttFeature">
                  <div class="ico">1</div>
                  <div><strong>Encontre</strong><small>as melhores opções</small></div>
                </div>
                <div class="fttFeature">
                  <div class="ico">2</div>
                  <div><strong>Compare</strong><small>preços e datas</small></div>
                </div>
                <div class="fttFeature">
                  <div class="ico">3</div>
                  <div><strong>Analise</strong><small>uso de milhas</small></div>
                </div>
                <div class="fttFeature">
                  <div class="ico">4</div>
                  <div><strong>Planeje</strong><small>sua próxima viagem</small></div>
                </div>
              </div>
            </div>
            <div class="visual" style="background-image:url('data:image/png;base64,{_hero_b64}')"></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )
else:
    st.markdown("## Buscador Inteligente de Passagens")
    st.caption("Pesquise voos, acompanhe preços e compare dinheiro com milhas.")

st.subheader("1. Pesquisa de passagens em dinheiro")
st.info(
    f"A pesquisa pode consumir no máximo **{len(comb)} consulta(s)** novas. "
    "Pesquisas idênticas já salvas são reaproveitadas."
)

ok = st.checkbox(f"Confirmo a pesquisa de até {len(comb)} consulta(s)")
force = st.checkbox("Ignorar cache e consultar novamente")

if st.button("Fazer varredura", type="primary", disabled=not ok, width="stretch"):
    rows = []
    novas = reap = 0
    prog = st.progress(0)

    for idx, (ida, volta) in enumerate(comb, 1):
        try:
            p = params_base(orig, dest, ida, volta, int(adultos), cab, stops)
            d, cached = consulta(p, force)
            reap += int(cached)
            novas += int(not cached)

            # Guarda o Price Insights retornado pela pesquisa para que o gráfico
            # de 30/60 dias possa ser atualizado apenas clicando no período.
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
        except Exception as e:
            st.warning(str(e))

        prog.progress(idx / len(comb))

    rows = sorted(rows, key=lambda x:(x["Preço (R$)"], x["Escalas"]))
    st.session_state["rank"] = rows
    st.session_state["uso"] = (novas, reap)
    st.session_state["ultima_pesquisa"] = {
        "orig": list(orig),
        "dest": list(dest),
        "ida": ida0,
        "volta": volta0,
        "adultos": int(adultos),
        "cabine": cab_pt,
        "conexoes": stop_pt,
    }

rank = st.session_state.get("rank", [])
if rank:
    novas, reap = st.session_state.get("uso", (0,0))
    st.success(
        f"Foram encontradas **{len(rank)} opções**. "
        f"Consultas novas: **{novas}** · reaproveitadas: **{reap}**."
    )

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
    st.dataframe(
        pd.DataFrame([{k:v for k,v in x.items() if not k.startswith("_")} for x in top]),
        width="stretch",
        hide_index=True,
        column_config={
            "Preço (R$)": st.column_config.NumberColumn(format="R$ %.2f")
        }
    )

    if volta0:
        st.subheader("Mais opções de ida e volta")

        labels = [
            f"{i+1}. {brl(x['Preço (R$)'])} | {x['Ida']} → {x['Volta']} | "
            f"{x['Origem']}→{x['Destino']} | {x['Companhia(s)']} | {x['Saída ida']}"
            for i,x in enumerate(top)
        ]

        choice = st.selectbox("Escolha uma das opções de ida", labels)
        sel = top[labels.index(choice)]

        if st.button("Buscar mais opções de volta para esta ida"):
            p = dict(sel["_params"])
            p["departure_token"] = sel["_token"]

            try:
                d, cached = consulta(p)
                rr = []

                for item in all_items(d):
                    s = summarize(item)
                    if s:
                        rr.append({
                            "Preço total (R$)": s["preco"],
                            "Origem": s["origem"],
                            "Destino": s["destino"],
                            "Companhia(s)": s["cias"],
                            "Saída": data_br(s["saida"]),
                            "Chegada": data_br(s["chegada"]),
                            "Escalas": s["escalas"],
                            "Duração": s["duracao"],
                            "Voos": s["voos"]
                        })

                if rr:
                    rdf = pd.DataFrame(rr)
                    rdf["Preço total (R$)"] = pd.to_numeric(
                        rdf["Preço total (R$)"],
                        errors="coerce"
                    )
                    st.session_state["retornos"] = rdf.sort_values(
                        "Preço total (R$)",
                        na_position="last"
                    )

            except Exception as e:
                st.error(str(e))
    else:
        st.caption("Pesquisa somente de ida: não há etapa de seleção de retorno.")

if volta0 and "retornos" in st.session_state:
    st.dataframe(
        st.session_state["retornos"],
        width="stretch",
        hide_index=True,
        column_config={
            "Preço total (R$)": st.column_config.NumberColumn(format="R$ %.2f")
        }
    )


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
hist_adultos = ultima.get("adultos", int(adultos))
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
    st.success("Histórico obtido do Google Flights / Price Insights.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Preço atual", brl(preco_atual_hist))
    c2.metric("Classificação", analise["nivel"])
    c3.metric("Média do período", brl(analise["media"]))
    c4.metric("Diferença para a média", f"{analise['diferenca_pct']:+.1f}%")

    graf = hist_google.copy()
    graf["Data"] = pd.to_datetime(graf["Data"])
    graf = graf.set_index("Data")
    graf["Preço atual"] = preco_atual_hist
    grafico_historico_google_style(graf.reset_index(), preco_atual_hist)
    st.caption(f"Referência atual: {brl(preco_atual_hist)} · Média: {brl(analise['media'])}")

    st.caption(
        f"No período selecionado: mínimo {brl(analise['minimo'])}, "
        f"mediana {brl(analise['mediana'])} e máximo {brl(analise['maximo'])}."
    )

elif preco_atual_hist > 0 and not hist_local.empty:
    st.info(
        "O Google Flights não forneceu Price Insights nesta pesquisa. "
        "O gráfico abaixo usa o histórico próprio das pesquisas feitas neste aplicativo."
    )
    h = hist_local.copy()
    h["capturado_em"] = pd.to_datetime(h["capturado_em"])
    h = h.rename(columns={"capturado_em":"Data", "preco":"Preço (R$)"}).set_index("Data")
    media = float(h["Preço (R$)"].mean())
    minimo = float(h["Preço (R$)"].min())
    maximo = float(h["Preço (R$)"].max())
    diferenca = ((preco_atual_hist-media)/media*100) if media else 0

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Preço atual", brl(preco_atual_hist))
    c2.metric("Média observada", brl(media))
    c3.metric("Menor observado", brl(minimo))
    c4.metric("Diferença para a média", f"{diferenca:+.1f}%")

    h["Preço atual"] = preco_atual_hist
    grafico_historico_google_style(h.reset_index(), preco_atual_hist)
    st.caption(f"Referência atual: {brl(preco_atual_hist)} · Média observada: {brl(media)}")

    if len(h) < 3:
        st.caption(
            "Ainda há poucos registros próprios para uma avaliação robusta. "
            "O histórico melhora à medida que você repete pesquisas da mesma viagem ao longo dos dias."
        )
else:
    st.info(
        "O Google Flights não retornou histórico para esta busca e ainda não há registros próprios suficientes. "
        "A partir de agora, cada pesquisa concluída salva o menor preço encontrado para formar seu histórico."
    )

st.caption(
    "Observação: no Streamlit Community Cloud, o armazenamento local pode ser reiniciado em atualizações ou reinícios do aplicativo. "
    "Por isso, o histórico próprio é complementar; quando Price Insights estiver disponível, ele é priorizado."
)

st.divider()
st.markdown('<div id="milhas"></div>', unsafe_allow_html=True)
st.subheader("3. Seus saldos de milhas")



saved = load_saldos()
c1, c2, c3 = st.columns(3)

lat = c1.number_input(
    "LATAM Pass",
    min_value=0,
    value=int(saved["LATAM Pass"]),
    step=1000
)
smi = c2.number_input(
    "Smiles",
    min_value=0,
    value=int(saved["Smiles"]),
    step=1000
)
azu = c3.number_input(
    "Azul Fidelidade",
    min_value=0,
    value=int(saved["Azul Fidelidade"]),
    step=1000
)

if st.button("Salvar saldos"):
    save_saldos({
        "LATAM Pass": lat,
        "Smiles": smi,
        "Azul Fidelidade": azu
    })
    st.success("Saldos salvos.")

st.divider()
st.markdown('<div id="precos"></div>', unsafe_allow_html=True)
st.subheader("4. Comparador dinheiro × milhas")

preco_ref = st.number_input(
    "Preço de referência em dinheiro — ida e volta (R$)",
    min_value=0.0,
    value=float(st.session_state.get("preco_ref", 2439.0)),
    step=10.0
)

st.caption(
    "Se você fizer uma busca acima, o menor preço encontrado será usado automaticamente como referência."
)


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
    dias = max((ida - date.today()).days, 0)
    adultos = max(int(adultos), 1)
    nacional = _rota_eh_brasil(origens, destinos)
    brasil_argentina = _rota_brasil_argentina(origens, destinos)

    if prefixo == "LATAM":
        if nacional:
            if dias >= 90:
                return 0.0, "Automática: isenta pela antecedência de 90 dias ou mais."
            return 34.0 * adultos, (
                "Automática: R$ 34,00 ida e volta por passageiro em resgate nacional "
                "feito com menos de 90 dias."
            )
        if brasil_argentina:
            if dias >= 120:
                return 0.0, "Automática: isenta pela antecedência de 120 dias ou mais."
            return 94.68 * adultos, (
                "Automática: referência publicada de R$ 94,68 ida e volta por passageiro "
                "para América do Sul com menos de 120 dias."
            )
        if dias >= 120:
            return 0.0, "Automática: isenta pela antecedência de 120 dias ou mais."
        return 220.92 * adultos, (
            "Automática: referência publicada de R$ 220,92 ida e volta por passageiro "
            "para demais voos internacionais com menos de 120 dias."
        )

    if prefixo == "AZUL":
        if dias >= 90:
            return 0.0, "Automática: referência de isenção para emissão com 90 dias ou mais."
        if nacional:
            return 69.80 * adultos, (
                "Automática: a partir de R$ 34,90 por passageiro/trecho no site/app "
                "(R$ 69,80 ida e volta)."
            )
        return 237.80 * adultos, (
            "Automática: a partir de R$ 118,90 por passageiro/trecho internacional "
            "no site/app (R$ 237,80 ida e volta)."
        )

    if prefixo == "SMILES":
        return 0.0, (
            "Variável: a Smiles não publica uma taxa única aplicável a toda emissão. "
            "Confirme o valor no checkout e ajuste este campo."
        )

    return 0.0, "Taxa não cadastrada automaticamente."

def _secret_float(nome, padrao):
    try:
        return float(_secret_or_env(nome, str(padrao)).replace(",", "."))
    except Exception:
        return float(padrao)

programas = [
    ("Smiles", smi, "SMILES"),
    ("LATAM Pass dinâmica", lat, "LATAM"),
    ("Azul Fidelidade", azu, "AZUL")
]

st.markdown("### Taxas de resgate e emissão")
st.caption(
    "Quando existe regra pública objetiva, o aplicativo calcula uma referência automaticamente. "
    "Smiles permanece variável. Taxas aeroportuárias/embarque e cobranças de parceiras podem ser adicionais."
)

st.markdown("### Valores de referência do milheiro")
st.caption(
    "Os campos abaixo vêm com referências cadastradas e continuam editáveis. "
    "Preço de compra é diferente do valor econômico atribuído às milhas que você já possui."
)

st.info(
    "Smiles: referência-base cadastrada de R$ 80,00 por 1.000 milhas para compra sem considerar bônus. "
    "Promoções com bônus reduzem o custo efetivo do milheiro. "
    "LATAM Pass e Azul: o aplicativo não inventa uma cotação atual; use o valor da oferta disponível para sua conta."
)

rows = []
status_programas = []
cols = st.columns(3)

for i, (nome, saldo, prefixo) in enumerate(programas):
    with cols[i]:
        st.markdown(f"### {nome}")
        st.metric("Seu saldo", pts(saldo))

        req = st.number_input(
            f"Milhas/pontos exigidos — {nome}",
            min_value=0,
            value=0,
            step=1000,
            key=f"r{i}",
            help="Quantidade total de milhas/pontos que o programa está cobrando pela emissão."
        )
        taxa_auto, taxa_origem = taxa_resgate_referencia(
            prefixo, orig, dest, ida0, adultos
        )
        tax = st.number_input(
            f"Taxa de resgate/emissão estimada (R$) — {nome}",
            min_value=0.0,
            value=float(taxa_auto),
            step=1.0,
            key=f"t{i}",
            help=(
                "Referência automática quando existe regra pública objetiva. "
                "O campo continua editável porque taxa de embarque, companhia parceira, "
                "categoria do cliente e o checkout podem alterar o valor final."
            )
        )
        st.caption(taxa_origem)

        compra_default = 80.0 if prefixo == "SMILES" else 0.0
        compra_padrao = _secret_float(f"{prefixo}_BUY_PRICE_PER_1000", compra_default)
        valor_padrao = _secret_float(f"{prefixo}_OWN_VALUE_PER_1000", 15.0)

        compra1000 = st.number_input(
            f"Preço atual para comprar 1.000 milhas (R$) — {nome}",
            min_value=0.0,
            value=float(compra_padrao),
            step=1.0,
            key=f"c{i}",
            help=(
                "Use o preço efetivo da promoção atual para comprar 1.000 milhas/pontos. "
                "Se você já tem saldo suficiente, esse valor não entra no desembolso."
            )
        )
        if prefixo == "SMILES":
            st.caption("Referência Smiles: R$ 80/1.000 antes de bônus promocionais. Fonte oficial consultada em 23/08/2026.")
        else:
            st.caption("Cotação automática oficial não disponível nesta versão; informe a oferta atual exibida para sua conta.")

        valor1000 = st.number_input(
            f"Quanto considero que valem 1.000 milhas que já possuo (R$) — {nome}",
            min_value=0.0,
            value=float(valor_padrao),
            step=1.0,
            key=f"v{i}",
            help=(
                "É um valor econômico atribuído às milhas que já estão no seu saldo. "
                "Não é o preço de compra do milheiro. Ele serve para comparar milhas usadas com o preço em dinheiro."
            )
        )

        if compra1000 == 0:
            st.caption("Preço de compra do milheiro: não informado. Preencha apenas se precisar comprar/completar milhas.")
        st.caption(
            f"Valor econômico usado para suas milhas já existentes: {brl(valor1000)} por 1.000."
        )

        if req == 0:
            status_programas.append({
                "Programa": nome,
                "Status": "Aguardando quantidade de milhas/pontos exigidos"
            })
        else:
            falt = max(req - saldo, 0)
            compra = (
                (falt/1000)*compra1000
                if falt > 0 and compra1000 > 0
                else (0 if falt == 0 else None)
            )

            if compra is None:
                status_programas.append({
                    "Programa": nome,
                    "Status": f"Faltam {pts(falt)} milhas/pontos; informe o preço atual para comprar 1.000 e completar o saldo"
                })
            else:
                imed = tax + compra
                econ = (min(req, saldo)/1000)*valor1000 + tax + compra

                rows.append({
                    "Opção": nome,
                    "Desembolso imediato": imed,
                    "Custo econômico": econ,
                    "Milhas exigidas": req,
                    "Milhas faltantes": falt,
                    "Saldo após emissão": max(saldo - req, 0)
                })

regra_fixa = fixed_table_rule(orig, dest)

if regra_fixa:
    st.markdown(
        f"### Simulador de tabela fixa {regra_fixa['programa']} — {regra_fixa['rota']}"
    )
    st.warning(
        "Esta seção é um simulador de referência. O aplicativo não confirma sozinho se existe assento elegível "
        "por tabela fixa na data pesquisada. Só inclua esta opção no ranking se você confirmar a disponibilidade "
        "no LATAM Pass/companhia parceira."
    )

    disponibilidade_fixa = st.selectbox(
        f"Você confirmou disponibilidade de assento elegível pela tabela fixa {regra_fixa['programa']}?",
        [
            "Ainda não verifiquei",
            "Verifiquei e NÃO há disponibilidade",
            "Verifiquei e HÁ disponibilidade"
        ],
        index=0
    )
    usar_fixa = disponibilidade_fixa == "Verifiquei e HÁ disponibilidade"

    st.caption(
        regra_fixa.get("observacao", "Confirme disponibilidade e regras diretamente com o programa.")
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

    ccontato, cregras = st.columns(2)

    with ccontato:
        if contato_url:
            st.link_button(
                regra_fixa.get("contato_label", "🔎 Verificar disponibilidade"),
                contato_url,
                type="primary",
                width="stretch"
            )
        else:
            st.info(
                f"Contato automático ainda não cadastrado para {regra_fixa['programa']}."
            )

    with cregras:
        if regra_fixa.get("regras_url"):
            st.link_button(
                regra_fixa.get("regras_label", "📋 Abrir regras oficiais"),
                regra_fixa["regras_url"],
                width="stretch"
            )
        else:
            st.info(
                f"Página oficial de regras ainda não cadastrada para {regra_fixa['programa']}."
            )

    st.info(
        regra_fixa.get(
            "orientacao",
            f"Confirme diretamente com {regra_fixa['programa']} a disponibilidade "
            "antes de incluir a tabela fixa no ranking."
        )
    )

    cabines_disponiveis = list(regra_fixa.get("tabelas_cabine", {}).keys())
    cabine_fixa = st.selectbox(
        "Cabine da tabela fixa",
        cabines_disponiveis,
        index=0
    )
    regra_cabine = regra_fixa["tabelas_cabine"][cabine_fixa]
    fixed_req_trecho = int(regra_cabine["milhas_por_trecho"])
    fixed_req = int(fixed_req_trecho * (2 if volta0 else 1))

    st.info(
        f"{cabine_fixa}: {pts(fixed_req_trecho)} milhas por trecho "
        f"({pts(fixed_req)} milhas para a viagem selecionada)."
    )

    st.link_button(
        f"📊 Ver tabela oficial LATAM — {cabine_fixa}",
        regra_cabine["url"],
        width="stretch"
    )

    fixed_tax_auto, fixed_tax_msg = taxa_resgate_referencia(
        "LATAM", orig, dest, ida0, adultos
    )
    fixed_tax = st.number_input(
        "Taxa de resgate/emissão estimada da tabela fixa (R$)",
        min_value=0.0,
        value=float(fixed_tax_auto),
        step=1.0,
        help=(
            "Referência automática da taxa de resgate LATAM. "
            "Taxas aeroportuárias e cobranças da companhia parceira podem ser adicionais."
        )
    )
    st.caption(fixed_tax_msg)
    fixed_buy = st.number_input(
        f"Preço atual para comprar 1.000 milhas {regra_fixa['programa']} e completar saldo (R$)",
        min_value=0.0,
        value=0.0,
        step=1.0
    )

    fixed_missing = max(fixed_req - lat, 0)

    a,b,c,d = st.columns(4)
    a.metric("Exigência estimada da viagem", f"{pts(fixed_req)} milhas")
    b.metric("Seu saldo LATAM", pts(lat))
    c.metric("Milhas faltantes", pts(fixed_missing))
    milhas_para_comprar = fixed_missing if fixed_missing > 0 else fixed_req
    max_milheiro = (
        max(preco_ref - fixed_tax, 0) / milhas_para_comprar * 1000
        if milhas_para_comprar else 0
    )
    d.metric(
        "Preço máximo do milheiro para valer a pena",
        f"{brl(max_milheiro)} / 1.000",
        help=(
            "Maior preço aproximado por 1.000 milhas necessárias para que a alternativa "
            "não ultrapasse o preço da passagem em dinheiro."
        )
    )
    st.caption(
        f"Até aproximadamente {brl(max_milheiro)} por 1.000 milhas necessárias, "
        "a tabela fixa pode continuar competitiva frente ao preço em dinheiro, "
        "antes de outras cobranças eventualmente aplicáveis."
    )

    if not usar_fixa:
        fixed_status = (
            "Tabela fixa fora do ranking. Primeiro confirme a disponibilidade de assento elegível no LATAM Pass."
            if disponibilidade_fixa == "Ainda não verifiquei"
            else "Você informou que não há disponibilidade; a tabela fixa não entra no ranking."
        )
    elif fixed_req <= 0:
        fixed_status = "Informe uma referência de milhas por trecho maior que zero."
    elif fixed_missing == 0:
        rows.append({
            "Opção": f"{regra_fixa['programa']} tabela fixa",
            "Desembolso imediato": fixed_tax,
            "Custo econômico": fixed_tax + (fixed_req/1000)*15,
            "Milhas exigidas": fixed_req,
            "Milhas faltantes": 0,
            "Saldo após emissão": lat - fixed_req
        })
        fixed_status = "Incluída no ranking. Confirme disponibilidade real antes de emitir."
    elif fixed_buy > 0:
        comp = fixed_missing/1000 * fixed_buy
        rows.append({
            "Opção": f"{regra_fixa['programa']} tabela fixa",
            "Desembolso imediato": fixed_tax + comp,
            "Custo econômico": fixed_tax + comp + (min(lat,fixed_req)/1000)*15,
            "Milhas exigidas": fixed_req,
            "Milhas faltantes": fixed_missing,
            "Saldo após emissão": 0
        })
        fixed_status = "Incluída no ranking usando o preço informado para completar o saldo."
    else:
        fixed_status = (
            f"Faltam {pts(fixed_missing)} milhas. Informe o preço atual para comprar 1.000 "
            "antes de incluir essa alternativa no ranking."
        )

    st.caption(f"Status: {fixed_status}")
else:
    st.markdown("### Tabelas fixas aplicáveis à rota")
    st.info(
        "Nenhuma regra de tabela fixa cadastrada corresponde à rota atual. "
        "As cotações dinâmicas dos programas continuam disponíveis normalmente."
    )

if status_programas:
    st.markdown("### Dados ainda faltantes")
    st.dataframe(
        pd.DataFrame(status_programas),
        width="stretch",
        hide_index=True
    )

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
    key=lambda x:(x["Desembolso imediato"], x["Custo econômico"])
)

for pos, x in enumerate(ranking, 1):
    x["Posição"] = pos

st.markdown("### Ranking automático")

rdf = pd.DataFrame(ranking)[[
    "Posição",
    "Opção",
    "Desembolso imediato",
    "Custo econômico",
    "Milhas exigidas",
    "Milhas faltantes",
    "Saldo após emissão"
]]

st.dataframe(
    rdf,
    width="stretch",
    hide_index=True,
    column_config={
        "Desembolso imediato": st.column_config.NumberColumn(format="R$ %.2f"),
        "Custo econômico": st.column_config.NumberColumn(format="R$ %.2f")
    }
)

v = ranking[0]

st.markdown("### Recomendação")

if v["Opção"] == "Dinheiro":
    st.success(
        f"Dinheiro é a melhor opção pelos dados informados. "
        f"Desembolso imediato: {brl(v['Desembolso imediato']).replace('$', r'\$')}. "
        f"Custo econômico estimado: {brl(v['Custo econômico']).replace('$', r'\$')}."
    )
else:
    st.success(
        f"{v['Opção']} é a melhor opção pelos dados informados. "
        f"Desembolso imediato: {brl(v['Desembolso imediato']).replace('$', r'\$')}. "
        f"Custo econômico estimado: {brl(v['Custo econômico']).replace('$', r'\$')}. "
        f"Economia de caixa frente ao dinheiro: "
        f"{brl(max(preco_ref - v['Desembolso imediato'], 0)).replace('$', r'\$')}."
    )

st.divider()
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
    "tipo": "Ida e volta" if volta0 else "Só ida",
    "cabine": cab_pt,
    "ida": data_br(ida0),
    "volta": data_br(volta0) if volta0 else "-",
    "adultos": int(adultos),
    "conexoes": stop_pt,
    "voos": [{k:v for k,v in x.items() if not k.startswith("_")} for x in rank[:20]] if rank else [],
    "preco_atual": float(st.session_state.get("preco_ref",0) or 0),
    "historico": hist_pdf,
    "saldos": {"LATAM Pass": lat, "Smiles": smi, "Azul Fidelidade": azu},
    "ranking_milhas": rdf if "rdf" in locals() else None,
    "tabela_fixa": tabela_fixa_pdf,
    "recomendacao": recomendacao_pdf,
}

st.divider()
st.markdown('<div id="relatorios"></div>', unsafe_allow_html=True)
st.markdown("""
<div style="
    background:#fff;border:1px solid #dfe7f0;border-radius:18px;
    padding:18px 20px;margin-top:10px;box-shadow:0 5px 18px rgba(8,34,74,.04);
">
  <div style="font-size:1.05rem;font-weight:800;color:#08224a;">Relatório completo em PDF</div>
  <div style="color:#718096;margin-top:4px;">
    Baixe um relatório completo com voos, gráficos, tabelas, comparação de milhas e recomendação final.
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
else:
    st.info("Faça uma pesquisa primeiro. O botão de PDF será liberado após os resultados.")


st.markdown("""
<div class="fttFooter">
Desenvolvido por Frederico Afonso Farias · © 2026 · Dados via SerpApi · Tenha uma excelente busca!
</div>
""",unsafe_allow_html=True)
