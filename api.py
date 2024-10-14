from flask import Flask, jsonify, request
from flasgger import Swagger
import requests
from bs4 import BeautifulSoup
import logging
import aiohttp
import time

app = Flask(__name__)
swagger = Swagger(app)

# Loglama ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache için önbellek süresi (saniye cinsinden, örneğin 1 saat = 3600 saniye)
CACHE_TIMEOUT = 3600
cache = {}

# Siteden verileri çekme fonksiyonu
async def get_border_data(start_date, end_date):
    url = f"https://www.und.org.tr/sinir-kapilari-yogunluk-durumu?START_DATE={start_date}&END_DATE={end_date}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                logger.error(f"Web sitesine erişilemedi, HTTP Durum Kodu: {response.status}")
                return None, f"Web sitesine erişilemedi, HTTP Durum Kodu: {response.status}"
            content = await response.text()
    
    soup = BeautifulSoup(content, "html.parser")
    
    # Verileri parse etme
    data = []
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        for row in rows:
            columns = row.find_all("td")
            data_row = [col.text.strip() for col in columns]
            if data_row:
                data.append(data_row)
    else:
        return None, "Veriler alınamadı, sayfada tablo bulunamadı."
    
    return data, None

# Verileri filtreleme fonksiyonu (belirli kapıları filtreler)
def filter_border_data(data, kapilar):
    if not kapilar:
        return data  # Kapı filtrelemesi yapılmadıysa, tüm verileri döner
    return [row for row in data if row[0] in kapilar]

# Cache kontrol fonksiyonu
def get_cached_data(key):
    if key in cache:
        data, timestamp = cache[key]
        if time.time() - timestamp < CACHE_TIMEOUT:  # Cache süresi kontrolü
            return data
        else:
            del cache[key]  # Cache süresi dolduysa, silinir
    return None

# Cache'e veri ekleme fonksiyonu
def set_cache_data(key, data):
    cache[key] = (data, time.time())

# API endpoint
@app.route('/border-data', methods=['GET'])
async def border_data():
    """
    Get border data based on the provided start and end dates.
    ---
    parameters:
      - name: start_date
        in: query
        type: string
        required: true
        description: Start date in YYYY-MM-DD format
      - name: end_date
        in: query
        type: string
        required: true
        description: End date in YYYY-MM-DD format
      - name: kapilar
        in: query
        type: array
        items:
          type: string
        required: false
        description: List of border names to filter the data
    responses:
      200:
        description: Successful response with border data
        schema:
          type: object
          properties:
            source:
              type: string
              description: Source of the data (cache or live)
            data:
              type: array
              items:
                type: array
                items:
                  type: string
      400:
        description: Bad request due to missing parameters
      500:
        description: Internal server error
    """
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    kapilar = request.args.getlist('kapilar')  # Kapı adı parametresi (isteğe bağlı)

    # Parametre kontrolü
    if not start_date or not end_date:
        logger.warning("Gerekli parametreler eksik: start_date ve end_date.")
        return jsonify({"error": "Lütfen start_date ve end_date parametrelerini sağlayın."}), 400

    # Cache anahtarı (aynı tarih ve kapı kombinasyonu için aynı anahtar)
    cache_key = f"{start_date}_{end_date}_{'_'.join(kapilar)}"
    
    # Cache kontrolü
    cached_data = get_cached_data(cache_key)
    if cached_data:
        logger.info("Cache kullanıldı.")
        return jsonify({"source": "cache", "data": cached_data})

    # Veriyi siteden çekme
    data, error = await get_border_data(start_date, end_date)
    if error:
        logger.error(f"Veri çekme hatası: {error}")
        return jsonify({"error": error}), 500

    # Veri filtreleme
    filtered_data = filter_border_data(data, kapilar)
    
    # Veriyi cache'e ekleme
    set_cache_data(cache_key, filtered_data)

    logger.info("Canlı veri kullanıldı.")
    return jsonify({"source": "live", "data": filtered_data})

if __name__ == "__main__":
    app.run(debug=True)
