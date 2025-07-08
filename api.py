from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel
import httpx
from bs4 import BeautifulSoup
import logging
import time
from datetime import datetime
import uvicorn
import os

# Loglama ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Uygulama oluşturma
app = FastAPI(
    title="Sınır Kapıları API",
    description="Sınır kapıları yoğunluk durumunu çeken API",
    version="1.0.0"
)

# CORS ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache için önbellek süresi (saniye cinsinden, örneğin 1 saat = 3600 saniye)
CACHE_TIMEOUT = 3600
cache: Dict[str, Tuple[List[List[str]], float]] = {}

# Yanıt modeli
class BorderResponse(BaseModel):
    source: str
    data: List[List[str]]

# Hata yanıt modeli
class ErrorResponse(BaseModel):
    error: str

# Verileri siteden çekme fonksiyonu
async def get_border_data(start_date: str, end_date: str) -> Tuple[Optional[List[List[str]]], Optional[str]]:
    url = f"https://www.und.org.tr/sinir-kapilari-yogunluk-durumu?START_DATE={start_date}&END_DATE={end_date}"
    
    async with httpx.AsyncClient() as client:
        try:
            
            response = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            })
            #response = await client.get(url)
            response.raise_for_status()  # HTTP hatası kontrolü
        except httpx.HTTPStatusError as e:
            logger.error(f"Web sitesine erişilemedi, HTTP Durum Kodu: {e.response.status_code}")
            return None, f"Web sitesine erişilemedi, HTTP Durum Kodu: {e.response.status_code}"
        except httpx.RequestError as e:
            logger.error(f"İstek hatası: {str(e)}")
            return None, f"İstek hatası: {str(e)}"
            
        content = response.text
    
    soup = BeautifulSoup(content, "html.parser")
    
    # Verileri parse etme
    data = []
    from bs4 import Tag

    table = soup.find("table")
    # Check if table is a Tag (not NavigableString or PageElement)
    if isinstance(table, Tag):
        rows = table.find_all("tr")
        for row in rows:
            if isinstance(row, Tag):
                columns = row.find_all("td")
                data_row = [col.text.strip() for col in columns]
                if data_row:
                    data.append(data_row)
    else:
        return None, "Veriler alınamadı, sayfada tablo bulunamadı."
    
    return data, None

# Verileri filtreleme fonksiyonu
def filter_border_data(data: List[List[str]], kapilar: List[str]) -> List[List[str]]:
    if not kapilar:
        return data  # Kapı filtrelemesi yapılmadıysa, tüm verileri döner
    return [row for row in data if row[0] in kapilar]

# Cache işlemleri için bağımlılık (dependency)
def cache_manager():
    def get_cached_data(key: str) -> Optional[List[List[str]]]:
        if key in cache:
            data, timestamp = cache[key]
            if time.time() - timestamp < CACHE_TIMEOUT:  # Cache süresi kontrolü
                return data
            else:
                del cache[key]  # Cache süresi dolduysa, silinir
        return None

    def set_cache_data(key: str, data: List[List[str]]) -> None:
        cache[key] = (data, time.time())
        
    return {"get": get_cached_data, "set": set_cache_data}

# API endpoint
@app.get(
    "/api/border-data", 
    response_model=BorderResponse,
    responses={
        200: {"model": BorderResponse, "description": "Başarılı yanıt"},
        400: {"model": ErrorResponse, "description": "Geçersiz istek parametreleri"},
        500: {"model": ErrorResponse, "description": "Sunucu hatası"}
    }
)
async def border_data(
    start_date: str = Query(..., description="Başlangıç tarihi (YYYY-MM-DD formatında)"),
    end_date: str = Query(..., description="Bitiş tarihi (YYYY-MM-DD formatında)"),
    kapilar: Optional[List[str]] = Query(None, description="Filtrelenecek sınır kapıları listesi"),
    cache_deps: Dict = Depends(cache_manager)
):
    """
    Belirtilen tarih aralığına göre sınır kapıları verilerini getirir.
    Opsiyonel olarak belirli sınır kapılarını filtrelemek için 'kapilar' parametresi kullanılabilir.
    """
    # Tarih formatı kontrolü
    try:
        # YYYY-MM-DD formatını kontrol et
        datetime.strptime(start_date, '%Y-%m-%d')
        datetime.strptime(end_date, '%Y-%m-%d')
    except ValueError:
        logger.warning("Geçersiz tarih formatı. YYYY-MM-DD formatı bekleniyor.")
        raise HTTPException(
            status_code=400, 
            detail="Geçersiz tarih formatı. YYYY-MM-DD formatında olmalıdır."
        )

    # Cache anahtarı (aynı tarih ve kapı kombinasyonu için aynı anahtar)
    cache_key = f"{start_date}_{end_date}_{'_'.join(kapilar or [])}"
    
    # Cache kontrolü
    cached_data = cache_deps["get"](cache_key)
    if cached_data:
        logger.info("Cache kullanıldı.")
        return {"source": "cache", "data": cached_data}

    # Veriyi siteden çekme
    data, error = await get_border_data(start_date, end_date)
    if error:
        logger.error(f"Veri çekme hatası: {error}")
        raise HTTPException(status_code=500, detail=error)

    # Veri filtreleme
    if data is None:
        logger.error("Veri alınamadı, data None döndü.")
        raise HTTPException(status_code=500, detail="Veri alınamadı.")
    filtered_data = filter_border_data(data, kapilar or [])
    
    # Veriyi cache'e ekleme
    cache_deps["set"](cache_key, filtered_data)

    logger.info("Canlı veri kullanıldı.")
    return {"source": "live", "data": filtered_data}

# Sağlık kontrolü endpoint'i
@app.get("/health", status_code=200)
def health_check():
    """API sağlık durumunu kontrol eder"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# Ana uygulama için çalıştırma fonksiyonu
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))  # Render'da PORT değişkeni atanır, yoksa 8000 kullanılır
    uvicorn.run(
        "api_usage:app", 
        host="0.0.0.0", 
        port=port, 
        reload=True
    )
