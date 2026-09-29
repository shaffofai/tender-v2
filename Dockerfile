# ============================================================================
# Tender-v2 — hujjat tekshiruv xizmati (API, worker, yuboruvchi — bitta rasm)
#
#     docker build -t shaffofai-tender-v2 .
#     docker compose up -d          # docker-compose.yml: baza + 3 xizmat
#
# Uchala xizmat SHU rasmdan, faqat kirish nuqtasi boshqa (compose `entrypoint`).
# Baza konteynerda EMAS — compose dagi alohida `db` xizmati.
# ============================================================================

# Asos Debian RELIZI bilan qotirilgan, faqat Python versiyasi bilan emas:
# qotirilmagan `python:3.X-slim` bir kuni bookworm dan trixie ga o'tib ketgan
# (platformaning boshqa xizmatlarida kuzatilgan). Python 3.11 — ishlab
# chiqarishda sinalgan versiya.
FROM python:3.11-slim-trixie

# Kirill va o'zbekcha matnlar to'g'ri ishlashi uchun UTF-8 majburiy
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Bog'liqliklar alohida qatlamda: kod o'zgarganda qayta o'rnatilmaydi ────
COPY requirements.txt .
RUN pip install -r requirements.txt

# ── Kod: xizmat qatlami, tekshiruv dvigateli va kirish nuqtalari ───────────
# Ilgari fayllar BITTAMA-BITTA sanalardi (Dockerfile, ornatish.sh, test
# manifesti — uch joyda) va yangi modul bir joyga qo'shilmay qolib ketardi.
# Endi ikki paket butunicha ko'chiriladi.
COPY app/ ./app/
COPY tender_engine/ ./tender_engine/
COPY main.py jobs_worker.py yuboruvchi.py api_server.py \
     korish.py kuzatuv.py ishga_tushir.py ./

# Build paytida SMOKE-TEST: yetishmagan modul ISHLAB CHIQARISHDA emas, shu
# yerda tutilsin. Zaxira zanjiri (`tiklash_*`) KECH (faqat buzuq faylda)
# import qilinadi — shuning uchun ular alohida sanab tekshiriladi.
RUN python -c "import main, jobs_worker, yuboruvchi, api_server, korish, kuzatuv, ishga_tushir" \
 && python -c "import app.worker.run, app.worker.cli, app.sender.cli, app.api.app, app.db.migrate" \
 && python -c "import app.tools.korish, app.tools.kuzatuv, app.tools.ishga_tushir" \
 && python -c "from tender_engine import reader, tiklash_rc4, tiklash_xom_xml, tiklash_nol_bayt, tiklash_xlsb" \
 && python jobs_worker.py --help > /dev/null \
 && python main.py --help > /dev/null \
 && python yuboruvchi.py --help > /dev/null \
 && python -m app.db.migrate --help > /dev/null

# Kiruvchi API porti (faqat `api` xizmati ishlatadi)
EXPOSE 8000

# ── Ildiz huquqisiz foydalanuvchi ─────────────────────────────────────────
RUN useradd --system --uid 10001 --create-home tender \
 && mkdir -p /data/downloads /data/etalon_cache \
 && chown -R tender:tender /app /data
USER tender

# Ish vaqtidagi papkalar — compose da volume bilan ulanadi. ETALON_CACHE_DIR
# muhim: bitta etalon o'nlab faylga ishlatiladi, volume bo'lmasa har qayta
# ishga tushirishda hammasi qaytadan yuklab olinadi.
ENV DOWNLOAD_DIR=/data/downloads \
    ETALON_CACHE_DIR=/data/etalon_cache \
    LOG_FILE=""

# ── Tiriklik (worker uchun standart; api/yuboruvchi compose da o'ziniki) ──
# FAQAT bazaga ulanish, sxema, etalon manbai va yozish huquqlari. Navbat
# uzunligi TEKSHIRILMAYDI: katta navbat normal ish holati, uni «nosog'lom»
# deyish cheksiz restart siklini keltirib chiqarardi.
HEALTHCHECK --interval=60s --timeout=30s --start-period=30s --retries=3 \
    CMD ["python", "jobs_worker.py", "--health"]

# Standart: worker, doimiy xizmat rejimi (navbat bo'shasa ham kutadi).
ENTRYPOINT ["python", "main.py"]
CMD ["--davomiy", "-y"]
