
import os, json, hashlib, statistics, hmac
from datetime import date, timedelta, datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Buscador Inteligente de Passagens",
    page_icon="✈️",
    layout="wide"
)

SERPAPI_URL = "https://serpapi.com/search.json"
BASE = Path.cwd()
DATA_DIR = BASE / "dados_usuario"
DATA_DIR.mkdir(exist_ok=True)
SALDOS_FILE = DATA_DIR / "meus_saldos.json"
CACHE_DIR = BASE / "cache_buscas"
CACHE_DIR.mkdir(exist_ok=True)

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
    return {
        "engine": "google_flights",
        "api_key": api_key(),
        "departure_id": ",".join(orig),
        "arrival_id": ",".join(dest),
        "outbound_date": ida.isoformat(),
        "return_date": volta.isoformat(),
        "type": 1,
        "travel_class": cabine,
        "adults": adultos,
        "stops": stops,
        "currency": "BRL",
        "gl": "br",
        "hl": "pt"
    }

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
            "por_trecho": 24000,
            "ida_volta": 48000,
            "observacao": (
                "Regra de referência cadastrada para esta rota. "
                "Confirme elegibilidade, companhia parceira, disponibilidade "
                "e regra vigente antes da emissão."
            )
        }
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

    st.title("🔐 Buscador de Passagens")
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

st.title("✈️ Buscador Inteligente de Passagens")
st.caption("Versão 10 Web — acesso privado + histórico de preços + milhas + ranking automático")

if api_key():
    st.success("SerpApi conectada.")
else:
    st.error("SERPAPI_API_KEY não encontrada.")

with st.sidebar:
    st.header("Pesquisa aérea")

    orig_txt = st.text_input("Origens", "GYN,BSB")
    dest_txt = st.text_input("Destinos", "EZE,AEP")

    ida0 = st.date_input(
        "Data principal de ida",
        date.today() + timedelta(days=30),
        min_value=date.today(),
        format="DD/MM/YYYY"
    )
    volta0 = st.date_input(
        "Data principal de volta",
        date.today() + timedelta(days=37),
        min_value=ida0,
        format="DD/MM/YYYY"
    )

    fi = st.selectbox("Flexibilidade da ida", [0,1,2,3,5,7], index=0)
    fv = st.selectbox("Flexibilidade da volta", [0,1,2,3,5,7], index=0)
    adultos = st.number_input("Adultos", 1, 9, 1)

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
comb = [(i,v) for i in flex(ida0,fi) for v in flex(volta0,fv) if v > i]

st.subheader("1. Pesquisa de passagens em dinheiro")
st.info(
    f"A varredura pode consumir no máximo **{len(comb)} consulta(s)** novas. "
    "Pesquisas idênticas já salvas são reaproveitadas."
)

ok = st.checkbox(f"Confirmo a varredura de até {len(comb)} consulta(s)")
force = st.checkbox("Ignorar cache e consultar novamente")

if st.button("🔎 Fazer varredura", type="primary", disabled=not ok):
    rows = []
    novas = reap = 0
    prog = st.progress(0)

    for idx, (ida, volta) in enumerate(comb, 1):
        try:
            p = params_base(orig, dest, ida, volta, int(adultos), cab, stops)
            d, cached = consulta(p, force)
            reap += int(cached)
            novas += int(not cached)

            for item in all_items(d):
                s = summarize(item)
                if s and isinstance(s["preco"], (int,float)) and s["token"]:
                    rows.append({
                        "Ida": data_br(ida),
                        "Volta": data_br(volta),
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

rank = st.session_state.get("rank", [])
if rank:
    novas, reap = st.session_state.get("uso", (0,0))
    st.success(
        f"Foram encontradas **{len(rank)} opções de ida**. "
        f"Consultas novas: **{novas}** · reaproveitadas: **{reap}**."
    )

    menor = rank[0]["Preço (R$)"]
    st.session_state["preco_ref"] = menor
    st.metric("Menor preço de ida e volta encontrado", brl(menor))

    top = rank[:20]
    st.dataframe(
        pd.DataFrame([{k:v for k,v in x.items() if not k.startswith("_")} for x in top]),
        width="stretch",
        hide_index=True,
        column_config={
            "Preço (R$)": st.column_config.NumberColumn(format="R$ %.2f")
        }
    )

    st.subheader("Mais opções de ida e volta")

    labels = [
        f"{i+1}. {brl(x['Preço (R$)'])} | {x['Ida']} → {x['Volta']} | "
        f"{x['Origem']}→{x['Destino']} | {x['Companhia(s)']} | {x['Saída ida']}"
        for i,x in enumerate(top)
    ]

    choice = st.selectbox("Escolha uma das opções de ida", labels)
    sel = top[labels.index(choice)]

    if st.button("🛬 Buscar mais opções de volta para esta ida"):
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

if "retornos" in st.session_state:
    st.dataframe(
        st.session_state["retornos"],
        width="stretch",
        hide_index=True,
        column_config={
            "Preço total (R$)": st.column_config.NumberColumn(format="R$ %.2f")
        }
    )


st.divider()
st.subheader("2. Histórico de preços")

periodo_hist = st.radio(
    "Comparar o preço atual com:",
    ["Últimos 30 dias", "Últimos 60 dias"],
    horizontal=True
)
dias_hist = 30 if periodo_hist == "Últimos 30 dias" else 60

preco_atual_hist = float(st.session_state.get("preco_ref", 0) or 0)
insights_raw = st.session_state.get("price_insights_raw", {}) or {}

hist_df = pd.DataFrame()
if insights_raw:
    hist_fake_container = {"price_insights": insights_raw}
    hist_df, _ = extrair_historico_preco(hist_fake_container, dias_hist)

if preco_atual_hist > 0 and (not hist_df.empty or insights_raw):
    analise = classificar_preco_atual(preco_atual_hist, hist_df, insights_raw)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Preço atual", brl(preco_atual_hist))
    c2.metric("Classificação", analise["nivel"])

    if analise["media"] is not None:
        c3.metric("Média do período", brl(analise["media"]))
        c4.metric(
            "Diferença para a média",
            f"{analise['diferenca_pct']:+.1f}%"
        )

        graf = hist_df.copy()
        graf["Data"] = pd.to_datetime(graf["Data"])
        graf = graf.set_index("Data")
        graf["Preço atual"] = preco_atual_hist

        st.line_chart(
            graf[["Preço (R$)", "Preço atual"]],
            width="stretch"
        )

        st.caption(
            f"No período selecionado: mínimo {brl(analise['minimo'])}, "
            f"mediana {brl(analise['mediana'])} e máximo {brl(analise['maximo'])}."
        )

        if analise["nivel"] in ["Muito baixo", "Baixo"]:
            st.success(
                "O preço atual está abaixo do padrão recente para esta pesquisa."
            )
        elif analise["nivel"] == "Na média":
            st.info(
                "O preço atual está próximo da média observada no período."
            )
        else:
            st.warning(
                "O preço atual está acima da média recente; pode valer a pena acompanhar antes de comprar."
            )
    else:
        nivel_google = (insights_raw or {}).get("price_level")
        faixa = (insights_raw or {}).get("typical_price_range")
        st.info(
            "O Google Flights retornou uma classificação de preço, mas não forneceu "
            "pontos históricos suficientes para desenhar o gráfico desta pesquisa."
        )
        if faixa and len(faixa) >= 2:
            st.write(
                f"Faixa típica informada pelo Google Flights: "
                f"**{brl(faixa[0])} a {brl(faixa[1])}**."
            )
else:
    st.info(
        "Faça uma pesquisa de passagens primeiro. Quando o Google Flights retornar "
        "Price Insights, o gráfico de 30 ou 60 dias aparecerá aqui automaticamente."
    )

st.divider()
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

if st.button("💾 Salvar saldos neste computador"):
    save_saldos({
        "LATAM Pass": lat,
        "Smiles": smi,
        "Azul Fidelidade": azu
    })
    st.success("Saldos salvos.")

st.divider()
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

programas = [
    ("Smiles", smi),
    ("LATAM Pass dinâmica", lat),
    ("Azul Fidelidade", azu)
]

rows = []
status_programas = []
cols = st.columns(3)

for i, (nome, saldo) in enumerate(programas):
    with cols[i]:
        st.markdown(f"### {nome}")
        st.metric("Seu saldo", pts(saldo))

        req = st.number_input(
            f"Milhas/pontos exigidos — {nome}",
            min_value=0,
            value=0,
            step=1000,
            key=f"r{i}"
        )
        tax = st.number_input(
            f"Taxas (R$) — {nome}",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key=f"t{i}"
        )
        compra1000 = st.number_input(
            f"Custo por 1.000 para completar (R$) — {nome}",
            min_value=0.0,
            value=0.0,
            step=1.0,
            key=f"c{i}"
        )
        valor1000 = st.number_input(
            f"Valor econômico por 1.000 das suas milhas (R$) — {nome}",
            min_value=0.0,
            value=15.0,
            step=1.0,
            key=f"v{i}"
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
                    "Status": f"Faltam {pts(falt)} e falta informar o custo por 1.000 para completar"
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
        f"### Tabela fixa {regra_fixa['programa']} — {regra_fixa['rota']}"
    )
    st.info(regra_fixa["observacao"])

    fixed_req = int(regra_fixa["ida_volta"])
    fixed_tax = st.number_input(
        "Taxas estimadas da tabela fixa (R$)",
        min_value=0.0,
        value=0.0,
        step=10.0
    )
    fixed_buy = st.number_input(
        f"Preço por 1.000 milhas {regra_fixa['programa']} para completar saldo (R$)",
        min_value=0.0,
        value=0.0,
        step=1.0
    )

    fixed_missing = max(fixed_req - lat, 0)

    a,b,c,d = st.columns(4)
    a.metric("Exigência ida e volta", f"{pts(fixed_req)} milhas")
    b.metric("Seu saldo LATAM", pts(lat))
    c.metric("Milhas faltantes", pts(fixed_missing))
    d.metric(
        "Ponto de equilíbrio",
        f"{brl((preco_ref-fixed_tax)/fixed_req*1000 if fixed_req else 0)} / 1.000"
    )

    if fixed_missing == 0:
        rows.append({
            "Opção": f"{regra_fixa['programa']} tabela fixa",
            "Desembolso imediato": fixed_tax,
            "Custo econômico": fixed_tax + (fixed_req/1000)*15,
            "Milhas exigidas": fixed_req,
            "Milhas faltantes": 0,
            "Saldo após emissão": lat - fixed_req
        })
        fixed_status = "Saldo suficiente; falta confirmar disponibilidade e taxas."
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
        fixed_status = (
            f"Faltam {pts(fixed_missing)} milhas; "
            "o custo informado para completar o saldo entrou na comparação."
        )
    else:
        fixed_status = (
            f"Faltam {pts(fixed_missing)} milhas; "
            "informe o preço por 1.000 para incluir a tabela fixa no ranking."
        )

    st.caption(f"Status da tabela fixa: {fixed_status}")
else:
    st.markdown("### Tabelas fixas aplicáveis à rota")
    st.info(
        "Nenhuma tabela fixa cadastrada no aplicativo corresponde às origens "
        "e destinos informados. As cotações dinâmicas de Smiles, LATAM Pass e "
        "Azul Fidelidade continuam disponíveis para comparação."
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
 
