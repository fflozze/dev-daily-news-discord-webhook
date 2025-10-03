import os, json, textwrap, re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse
import requests

# ---------- ENV ----------
OPENAI_API_KEY      = os.environ["OPENAI_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
MODEL        = os.getenv("MODEL", "gpt-4.1-mini")
HOURS        = int(os.getenv("HOURS", "24"))
LOCALE       = os.getenv("LOCALE", "fr-FR")
SOURCE_LANGS = os.getenv("SOURCE_LANGS", "fr,en")  # FR + EN
TZ           = os.getenv("TIMEZONE", "Europe/Paris")
COLOR        = int(os.getenv("EMBED_COLOR", "15105570"))  # 0xE67E22 (orange)

# Limites de contenu (anti-dup)
MAX_BULLETS = int(os.getenv("MAX_BULLETS", "8"))
MAX_LINKS   = int(os.getenv("MAX_LINKS", "12"))

now_utc   = datetime.now(timezone.utc)
since_utc = now_utc - timedelta(hours=HOURS)
date_str  = now_utc.strftime("%Y-%m-%d")

# ---------- Discord limits (extra safe) ----------
DISCORD_MAX_EMBEDS   = 10     # embeds per message
DISCORD_MAX_EMBED    = 6000   # hard API limit
EMBED_TARGET_BUDGET  = 3500   # aggressive target to keep margin
DISCORD_MAX_TITLE    = 256
DISCORD_MAX_DESC     = 4096
FIELD_HARD_MAX       = 1024
FIELD_SOFT_MAX       = 450    # our target (<< 1024)
DESC_SOFT_MAX        = 180    # short description

# ---------- Helpers: normalize / dedup ----------
JUNK_PATTERNS = [
    r"^\s*share with your followers!?$",
    r"^\s*publish$",
    r"^\s*don'?t show again$",
    r"^cookies? (policy|settings)",
    r"^accept (all|cookies)",
    r"^subscribe",
    r"^sign in",
    r"^log in",
    r"^read more",
    r"^continuer sans accepter",
    r"^param(è|e)tres? de confidentialit",
]

def _is_junk_line(line: str) -> bool:
    s = (line or "").strip().lower()
    if not s:
        return True
    for pat in JUNK_PATTERNS:
        if re.search(pat, s):
            return True
    return False

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\r", "")
    # drop markdown headings, bullets artifacts
    s = re.sub(r"^\s*#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"^[\-\*\•]\s*", "", s, flags=re.MULTILINE)
    # compress spaces
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()

def _fingerprint(s: str) -> str:
    """Key for dedup of bullets: lowercase, drop punctuation, collapse spaces."""
    s = _normalize_text(s).lower()
    # remove URLs to avoid diff only by utm
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[^\w]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _canonical_url(u: str) -> str:
    """Normalize URL for link dedup: lower host, drop query/fragment & trailing slash."""
    if not u:
        return ""
    m = re.search(r"(https?://\S+)", u)
    if m:
        u = m.group(1)
    try:
        p = urlparse(u.strip())
        host = (p.netloc or "").lower()
        path = (p.path or "/")
        # strip trailing slash except root
        if len(path) > 1 and path.endswith("/"):
            path = path[:-1]
        # drop common tracking queries completely
        new = p._replace(netloc=host, path=path, params="", query="", fragment="")
        return urlunparse(new)
    except Exception:
        return u.strip()

def _dedup_lines(lines, key_fn, limit):
    seen, out = set(), []
    for ln in lines:
        ln_norm = _normalize_text(ln)
        if not ln_norm or _is_junk_line(ln_norm):
            continue
        key = key_fn(ln_norm)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(ln_norm)
        if limit and len(out) >= limit:
            break
    return out

def _lines_from_markdown_block(md: str):
    """Split a markdown block into 'logical' lines (bullets or paragraphs)."""
    md = (md or "").strip()
    if not md:
        return []
    # prefer bullet lines
    bullet_like = re.findall(r"(?m)^\s*(?:\-|\*|•)\s+.+$", md)
    if bullet_like:
        return [re.sub(r"^\s*(?:\-|\*|•)\s+", "", x).strip() for x in bullet_like]
    # otherwise split on blank lines
    parts = re.split(r"\n\s*\n", md)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # split long paragraphs into sentences-ish chunks to avoid 2k fields
        if len(p) > 600:
            for i in range(0, len(p), 300):
                out.append(p[i:i+300].strip())
        else:
            out.append(p)
    return out

def _format_bullets_md(lines):
    return "\n".join(f"- {ln}" for ln in lines)

def _extract_link_lines(md: str):
    """Return raw lines likely to contain links."""
    md = (md or "").strip()
    if not md:
        return []
    # one per line
    lines = [x.strip() for x in md.splitlines() if x.strip()]
    return lines

def _format_links_md(lines):
    return "\n".join(f"- {ln}" for ln in lines)

# ---------- Discord building / shrinking ----------
def _text_len(s: str) -> int:
    return len(s or "")

def chunk_text(txt: str, size: int = FIELD_SOFT_MAX):
    """Split by lines; hard-split very long lines (e.g., URLs)."""
    txt = (_normalize_text(txt) or "")
    if not txt:
        return []
    lines, chunks, buf = txt.splitlines(), [], ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        add_len = len(line) + (0 if not buf else 1)
        if len(buf) + add_len > size:
            if buf:
                chunks.append(buf)
            if len(line) > size:
                for i in range(0, len(line), size):
                    chunks.append(line[i:i+size])
                buf = ""
            else:
                buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)
    return [c.strip()[:size] for c in chunks if c.strip()]

def _clean_embed(e: dict) -> dict:
    """Remove None/empty & clamp base sizes."""
    out = {}
    for k, v in e.items():
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        if k == "title":
            out[k] = v[:DISCORD_MAX_TITLE]
        elif k == "description":
            out[k] = v[:DISCORD_MAX_DESC]
        elif k == "fields":
            vv = []
            for f in (v or []):
                name = _normalize_text(f.get("name") or "")
                value = _normalize_text(f.get("value") or "")
                if name and value:
                    if len(value) > FIELD_SOFT_MAX:
                        value = value[:FIELD_SOFT_MAX] + "…"
                    value = value[:FIELD_HARD_MAX]
                    vv.append({"name": name[:256], "value": value})
            if vv:
                out[k] = vv
        else:
            out[k] = v
    return out

def _embed_size(e: dict) -> int:
    size = 0
    size += _text_len(e.get("title"))
    size += _text_len(e.get("description"))
    for f in e.get("fields") or []:
        size += _text_len(f.get("name")) + _text_len(f.get("value"))
    footer = e.get("footer") or {}
    size += _text_len(footer.get("text"))
    return size

def _shrink_to_fit(e: dict, target=EMBED_TARGET_BUDGET):
    """
    Reduce desc/field to pass under 'target', then <6000 if needed.
    Aggressive stepwise cuts.
    """
    e = _clean_embed(e)
    if e.get("description") and len(e["description"]) > DESC_SOFT_MAX:
        e["description"] = e["description"][:DESC_SOFT_MAX] + "…"
        e = _clean_embed(e)

    guard = 0
    while _embed_size(e) > target and guard < 100:
        guard += 1
        desc = e.get("description")
        if desc and len(desc) > 90:
            e["description"] = desc[:-60] + "…"
            e = _clean_embed(e)
            continue
        if e.get("fields"):
            f = e["fields"][0]
            val = f["value"]
            if len(val) > 120:
                f["value"] = val[:-80] + "…"
                e = _clean_embed(e)
                continue
        break

    guard = 0
    while _embed_size(e) > DISCORD_MAX_EMBED and guard < 100:
        guard += 1
        desc = e.get("description")
        if desc and len(desc) > 30:
            e["description"] = desc[:-30] + "…"
            e = _clean_embed(e)
            continue
        if e.get("fields"):
            f = e["fields"][0]
            val = f["value"]
            if len(val) > 50:
                f["value"] = val[:-50] + "…"
                e = _clean_embed(e)
                continue
        if e.get("description"):
            e["description"] = ""
            e = _clean_embed(e)
            continue
        if e.get("fields"):
            e["fields"].pop(0)
            e = _clean_embed(e)
            continue
        break
    return e

def _retry_shrink_and_send(payload, max_retries=3):
    for attempt in range(max_retries + 1):
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=30)
        if r.ok:
            return
        if "Embed size exceeds maximum size of 6000" not in r.text:
            raise RuntimeError(f"Discord payload error: {r.status_code} {r.text}")

        # more aggressive shrink for retry
        next_embeds = []
        for em in payload.get("embeds", []):
            if em.get("description"):
                limits = [120, 80, 50]
                lim = limits[min(attempt, len(limits)-1)]
                em["description"] = _normalize_text(em["description"])[:lim] + "…"
            if em.get("fields"):
                val = em["fields"][0]["value"]
                limits = [350, 250, 200]
                lim = limits[min(attempt, len(limits)-1)]
                em["fields"][0]["value"] = _normalize_text(val)[:lim] + "…"
            em = _shrink_to_fit(em, target=3000)
            next_embeds.append(_clean_embed(em))
        payload = {"embeds": next_embeds}
    raise RuntimeError("Discord 400 after retries: embeds still exceed size after aggressive shrinking.")

def _send_embeds_in_batches(embeds: list):
    """Send in batches (≤10). Shrink + retry if needed."""
    for i in range(0, len(embeds), DISCORD_MAX_EMBEDS):
        batch = embeds[i:i+DISCORD_MAX_EMBEDS]
        safe_batch = []
        for j, em in enumerate(batch):
            if j > 0 and "footer" in em:
                em.pop("footer", None)  # footer only on first embed per request
            em = _shrink_to_fit(em, target=EMBED_TARGET_BUDGET)
            if _embed_size(em) > EMBED_TARGET_BUDGET:
                em = _shrink_to_fit(em, target=3000)
            safe_batch.append(_clean_embed(em))
        payload = {"embeds": [em for em in safe_batch if em.get("title") or em.get("description") or em.get("fields")]}
        _retry_shrink_and_send(payload, max_retries=3)

def post_discord_embeds(title: str, description: str, bullet_text: str, links_text: str):
    """
    Strategy:
      - Embed #1: title + short desc + footer (no fields)
      - Then: 1 field per embed for 'Highlights' chunks, then 'Sources' chunks
      - Multiple messages if >10 embeds
    """
    title = (title or "").strip()[:DISCORD_MAX_TITLE]
    description = _normalize_text(description)
    if len(description) > DESC_SOFT_MAX:
        description = description[:DESC_SOFT_MAX] + "…"

    first_embed = {
        "title": title,
        "description": description if description else None,
        "color": COLOR,
        "footer": {"text": f"Veille Dev • {date_str} • Window: {HOURS}h • {TZ}"},
    }
    first_embed = _shrink_to_fit(first_embed, target=EMBED_TARGET_BUDGET)

    embeds = [first_embed]

    bullet_chunks = chunk_text(bullet_text, FIELD_SOFT_MAX) if bullet_text else []
    link_chunks   = chunk_text(links_text,  FIELD_SOFT_MAX) if links_text else []

    for idx, ch in enumerate(bullet_chunks):
        e = {"color": COLOR, "fields": [{"name": "Highlights" if idx == 0 else "Highlights (suite)", "value": ch}]}
        embeds.append(_shrink_to_fit(e, target=EMBED_TARGET_BUDGET))

    for idx, ch in enumerate(link_chunks):
        e = {"color": COLOR, "fields": [{"name": "Sources" if idx == 0 else "Sources (suite)", "value": ch}]}
        embeds.append(_shrink_to_fit(e, target=EMBED_TARGET_BUDGET))

    _send_embeds_in_batches(embeds)

# ---------- OpenAI (Responses API + web_search tool) ----------
def call_openai_websearch(prompt: str) -> str:
    """
    Call OpenAI Responses API with 'web_search' tool enabled.
    Returns Markdown.
    """
    url = "https://api.openai.com/v1/responses"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    body = {
        "model": MODEL,
        "input": prompt,
        "tools": [{"type": "web_search"}],
        "tool_choice": "auto",
    }
    r = requests.post(url, headers=headers, json=body, timeout=180)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and data.get("output_text"):
        return data["output_text"].strip()

    try:
        chunks = []
        for blk in data.get("output", []):
            for c in blk.get("content", []):
                t = c.get("text")
                if t:
                    chunks.append(t)
        if chunks:
            return "".join(chunks).strip()
    except Exception:
        pass

    return json.dumps(data, ensure_ascii=False)[:4000]

# ---------- Markdown parsing ----------
SECTION_LINKS_RE = re.compile(r"^\s*#{0,3}\s*links?\s*$", re.IGNORECASE | re.MULTILINE)
SECTION_LIENS_RE = re.compile(r"^\s*#{0,3}\s*liens?\s*$", re.IGNORECASE | re.MULTILINE)

def split_summary_links(md: str, fallback_title: str):
    """Return (title, bullets, links) from model Markdown output."""
    md = (md or "").strip()
    if not md:
        return fallback_title, "", ""

    title_match = re.search(r"^\s*#+\s*(.+)", md)
    title = title_match.group(1).strip() if title_match else fallback_title

    parts = SECTION_LINKS_RE.split(md)
    if len(parts) < 2:
        parts = SECTION_LIENS_RE.split(md)

    if len(parts) >= 2:
        before = parts[0].strip()
        after  = parts[1].strip()
        bullets = before
        links   = after
    else:
        bullets = md
        links = ""

    if title_match:
        start = title_match.end()
        if len(md) > start:
            bullets = md[start:].strip()

    return title, bullets, links

# ---------- Prompt (DEV-only) ----------
def build_prompt():
    """
    DEV NEWS scope (strict dev):
      - Languages: Python, JS/TS, Go, Rust, Java, C#, etc.
      - Frameworks/Libraries: React, Vue, Angular, Svelte, Django, FastAPI, Spring, .NET, etc.
      - Tooling: package managers, build tools, linters/formatters, IDEs, CLIs
      - Releases/Changelogs: major/minor, breaking changes, deprecations
      - Cloud/DevOps/CI-CD: containers, serverless, IaC, performance
      - Security for devs: advisories (CVE) impacting dev/runtime toolchains, supply chain (npm/pypi/crates)
      - Standards/Specs: TC39, WHATWG, W3C, IETF, DB ecosystems
      OUT-OF-SCOPE: corporate finance/news unrelated to dev, generic AI policy, city admin platforms sans nouveauté dev,
                     pop-ups/junk UI texts.
    """
    langs_note = SOURCE_LANGS.replace(",", ", ")
    return textwrap.dedent(f"""
    Tu es un agent de veille **Développement / Software Engineering**. Ne garde que des infos
    **centrées sur le développement** (langages, frameworks, toolchains, releases, DevOps/CI, specs/standards).

    Tâche :
    - Utilise la **recherche web** pour identifier les **actualités dev** publiées entre
      **{since_utc.strftime("%Y-%m-%d %H:%M UTC")}** et **{now_utc.strftime("%Y-%m-%d %H:%M UTC")}**.
    - **Sources à considérer** : en **français et en anglais** ({langs_note}). Priorise la **source primaire**
      (notes de version/changelogs officiels, blogs des projets, RFC/propositions TC39, etc.) et les médias réputés.
      **Évite absolument les doublons** d’un même événement.

    Rendu en **français ({LOCALE})** et en **Markdown** :
    1) Un titre : "Veille Dev — {date_str} (dernières {HOURS} h)".
    2) Un **résumé global** (2–3 phrases).
    3) **5–8 puces** factuelles (versions, breaking changes, composants impactés, dates, éditeurs) — **toutes distinctes**.
    4) Une section **Liens / Links** (10–12 items max) au format :
       - **[FR]** ou **[EN]** — Titre court — URL (ajoute la **date/heure** si dispo).
       (Marque chaque lien avec [FR] ou [EN] selon la langue de la source.)
    5) Cite **uniquement des sources réelles** (URLs visibles). Indique [paywall] si nécessaire.
    6) **Aucun doublon**. Aucune invention ; si incertain, précise-le. Pas de textes d'UI (share/publish/cookies).

    Format strictement Markdown. Pas de blocs de code inutiles.
    """).strip()

# ---------- Post-processing: dedup bullets & links ----------
def clean_bullets_and_links(bullets_md: str, links_md: str):
    # Bullets
    bullet_lines = _lines_from_markdown_block(bullets_md)
    bullet_lines = _dedup_lines(bullet_lines, key_fn=_fingerprint, limit=MAX_BULLETS)

    # Links
    raw_link_lines = _extract_link_lines(links_md)
    link_lines = _dedup_lines(raw_link_lines, key_fn=lambda s: _canonical_url(s), limit=None)
    # re-limit after dedup
    link_lines = link_lines[:MAX_LINKS]

    return _format_bullets_md(bullet_lines), _format_links_md(link_lines)

# ---------- Main ----------
def main():
    prompt = build_prompt()
    try:
        md = call_openai_websearch(prompt)
    except Exception as e:
        err = f"⚠️ Erreur durant la recherche/synthèse : {e}"
        post_discord_embeds(
            title=f"Veille Dev — {date_str} (dernières {HOURS} h)",
            description=err,
            bullet_text="",
            links_text=""
        )
        raise

    if not md or len(md.strip()) < 30:
        post_discord_embeds(
            title=f"Veille Dev — {date_str} (dernières {HOURS} h)",
            description="Aucune sortie exploitable retournée par l'IA (clé API/modèle ?).",
            bullet_text="",
            links_text=""
        )
        return

    fallback_title = f"Veille Dev — {date_str} (dernières {HOURS} h)"
    title, bullets, links = split_summary_links(md, fallback_title)

    # Description: first short paragraph BEFORE dedup (garde le résumé initial)
    first_para = re.split(r"\n\s*\n", (_normalize_text(bullets) or "").strip(), maxsplit=1)[0] if bullets.strip() else ""
    description = first_para

    # Dedup bullets & links, limit counts
    bullets_body, links_clean = clean_bullets_and_links(bullets, links)

    post_discord_embeds(
        title=title,
        description=description,
        bullet_text=bullets_body,
        links_text=links_clean
    )

if __name__ == "__main__":
    main()
