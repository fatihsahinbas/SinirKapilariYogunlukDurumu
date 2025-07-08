from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field
import httpx
from bs4 import BeautifulSoup
import logging
import time
from datetime import datetime
import uvicorn
import json

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Application instance
app = FastAPI(
    title="Border Gates Traffic API",
    description="""
    🚛 **Real-time Border Gates Traffic Monitoring API**
    
    Get live traffic density data for Turkish border gates. Perfect for:
    - Logistics companies planning cross-border transport
    - Travel agencies optimizing border crossing times
    - Analytics platforms tracking trade flows
    - Mobile apps for travelers and freight operators
    
    **Features:**
    ✅ Real-time traffic density data  
    ✅ Historical data access  
    ✅ Smart caching for optimal performance  
    ✅ Flexible filtering by specific border gates  
    ✅ RESTful API with comprehensive documentation  
    
    **Data Source:** Turkish Road Transport Operators Association (UND)
    
    ---
    *Powered by JojAPI.com - Your gateway to premium APIs*
    """,
    version="2.0.0",
    contact={
        "name": "JojAPI Support",
        "url": "https://jojapi.com/support",
        "email": "support@jojapi.com"
    },
    license_info={
        "name": "API License",
        "url": "https://jojapi.com/license"
    },
    servers=[
        {
            "url": "https://api.jojapi.com",
            "description": "Production server"
        },
        {
            "url": "https://staging-api.jojapi.com", 
            "description": "Staging server"
        }
    ]
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Configuration
CACHE_TIMEOUT_SECONDS = 3600  # 1 hour
cache: Dict[str, Tuple[List[List[str]], float]] = {}

# Response Models
class BorderGateData(BaseModel):
    """Individual border gate traffic data"""
    gate_name: str = Field(..., description="Name of the border gate")
    country: str = Field(..., description="Destination country")
    traffic_density: str = Field(..., description="Current traffic density level")
    wait_time: str = Field(..., description="Estimated waiting time")
    last_updated: str = Field(..., description="Last update timestamp")

class BorderGatesResponse(BaseModel):
    """Main API response model"""
    success: bool = Field(True, description="Request success status")
    source: str = Field(..., description="Data source: 'cache' or 'live'")
    total_gates: int = Field(..., description="Total number of border gates returned")
    date_range: Dict[str, str] = Field(..., description="Queried date range")
    data: List[List[str]] = Field(..., description="Raw border gate data matrix")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    
    class Config:
        schema_extra = {
            "example": {
                "success": True,
                "source": "live",
                "total_gates": 15,
                "date_range": {
                    "start_date": "01-12-2024",
                    "end_date": "31-12-2024"
                },
                "data": [
                    ["Kapıkule", "Bulgaria", "Normal", "30-45 min", "2024-12-15 14:30"],
                    ["Hamzabeyli", "Bulgaria", "Busy", "60-90 min", "2024-12-15 14:25"]
                ],
                "metadata": {
                    "cache_duration": "3600 seconds",
                    "api_version": "2.0.0"
                }
            }
        }

class ErrorResponse(BaseModel):
    """Error response model"""
    success: bool = Field(False, description="Request success status")
    error_code: str = Field(..., description="Error classification code")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[str] = Field(None, description="Additional error details")
    
    class Config:
        schema_extra = {
            "example": {
                "success": False,
                "error_code": "INVALID_DATE_FORMAT",
                "message": "Invalid date format. Please use DD-MM-YYYY format.",
                "details": "Expected format: 15-12-2024, received: 2024-12-15"
            }
        }

# Business Logic Functions
async def fetch_border_data(start_date: str, end_date: str) -> Tuple[Optional[List[List[str]]], Optional[str]]:
    """
    Fetch border gate data from the official source
    
    Args:
        start_date: Start date in DD-MM-YYYY format
        end_date: End date in DD-MM-YYYY format
        
    Returns:
        Tuple of (data_matrix, error_message)
    """
    url = f"https://www.und.org.tr/sinir-kapilari-yogunluk-durumu?START_DATE={start_date}&END_DATE={end_date}"
    headers = {
        "User-Agent": "JojAPI-BorderGates/2.0 (https://jojapi.com/border-gates)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
        soup = BeautifulSoup(response.text, "html.parser")
        data_matrix = []
        
        table = soup.find("table")
        if not table or not hasattr(table, "find_all"):
            return None, "No data table found on the source website"

        # Ensure table is a Tag before calling find_all
        from bs4.element import Tag
        if not isinstance(table, Tag):
            return None, "Table element is not valid"

        rows = table.find_all("tr")
        for row in rows:
            if not isinstance(row, Tag):
                continue
            columns = row.find_all("td")
            if columns:
                row_data = [col.get_text(strip=True) for col in columns]
                if row_data and any(row_data):  # Skip empty rows
                    data_matrix.append(row_data)
                    
        if not data_matrix:
            return None, "No border gate data found in the specified date range"
            
        return data_matrix, None
        
    except httpx.TimeoutException:
        logger.error("Request timeout while fetching border data")
        return None, "Request timeout - please try again later"
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code} while fetching data")
        return None, f"Data source unavailable (HTTP {e.response.status_code})"
    except Exception as e:
        logger.error(f"Unexpected error while fetching data: {str(e)}")
        return None, "Unexpected error occurred while fetching data"

def apply_border_filter(data: List[List[str]], gate_names: Optional[List[str]]) -> List[List[str]]:
    """
    Filter border gate data by specific gate names
    
    Args:
        data: Raw data matrix
        gate_names: List of gate names to filter by
        
    Returns:
        Filtered data matrix
    """
    if not gate_names:
        return data
    
    # Case-insensitive filtering
    gate_names_lower = [name.lower() for name in gate_names]
    return [
        row for row in data 
        if row and row[0].lower() in gate_names_lower
    ]

def validate_date_format(date_string: str) -> bool:
    """Validate DD-MM-YYYY date format"""
    try:
        datetime.strptime(date_string, '%d-%m-%Y')
        return True
    except ValueError:
        return False

# Cache Management Dependency
class CacheManager:
    """Smart caching system for API responses"""
    
    def __init__(self):
        self.cache = cache
        self.timeout = CACHE_TIMEOUT_SECONDS
    
    def get_cached_data(self, key: str) -> Optional[List[List[str]]]:
        """Retrieve data from cache if not expired"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.timeout:
                logger.info(f"Cache hit for key: {key}")
                return data
            else:
                # Remove expired cache entry
                del self.cache[key]
                logger.info(f"Cache expired for key: {key}")
        return None
    
    def set_cached_data(self, key: str, data: List[List[str]]) -> None:
        """Store data in cache with timestamp"""
        self.cache[key] = (data, time.time())
        logger.info(f"Data cached for key: {key}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        active_entries = len(self.cache)
        return {
            "active_entries": active_entries,
            "timeout_seconds": self.timeout,
            "cache_size_mb": sum(len(str(data)) for data, _ in self.cache.values()) / (1024 * 1024)
        }

def get_cache_manager() -> CacheManager:
    """Dependency injection for cache manager"""
    return CacheManager()

# API Endpoints
@app.get(
    "/border-gates",
    response_model=BorderGatesResponse,
    responses={
        200: {
            "model": BorderGatesResponse,
            "description": "Successfully retrieved border gate data"
        },
        400: {
            "model": ErrorResponse,
            "description": "Invalid request parameters"
        },
        503: {
            "model": ErrorResponse,
            "description": "Data source temporarily unavailable"
        }
    },
    tags=["Border Gates"],
    summary="Get Border Gate Traffic Data",
    description="""
    **Retrieve real-time traffic density data for Turkish border gates**
    
    This endpoint provides current traffic conditions and estimated waiting times 
    for border crossings. Data is updated regularly from official sources.
    
    **Use Cases:**
    - Plan optimal border crossing times
    - Monitor traffic trends for logistics optimization
    - Build travel advisory applications
    - Analyze cross-border trade patterns
    
    **Rate Limits:** 
    - Free tier: 100 requests/day
    - Pro tier: 10,000 requests/day
    - Enterprise: Unlimited
    
    **Data Freshness:** Updated every 15-30 minutes
    """
)
async def get_border_gate_data(
    start_date: str = Query(
        ..., 
        description="Start date in DD-MM-YYYY format (e.g., 15-12-2024)",
        example="01-12-2024",
        regex=r"^\d{2}-\d{2}-\d{4}$"
    ),
    end_date: str = Query(
        ..., 
        description="End date in DD-MM-YYYY format (e.g., 31-12-2024)",
        example="31-12-2024",
        regex=r"^\d{2}-\d{2}-\d{4}$"
    ),
    gates: Optional[List[str]] = Query(
        None, 
        description="Filter by specific border gate names (case-insensitive)",
        example=["Kapıkule", "Hamzabeyli"]
    ),
    cache_manager: CacheManager = Depends(get_cache_manager)
):
    """Main endpoint for retrieving border gate traffic data"""
    
    # Validate date formats
    if not validate_date_format(start_date):
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "INVALID_START_DATE",
                "message": "Invalid start date format. Use DD-MM-YYYY format.",
                "details": f"Received: {start_date}, Expected format: DD-MM-YYYY"
            }
        )
    
    if not validate_date_format(end_date):
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "INVALID_END_DATE", 
                "message": "Invalid end date format. Use DD-MM-YYYY format.",
                "details": f"Received: {end_date}, Expected format: DD-MM-YYYY"
            }
        )
    
    # Validate date range
    start_dt = datetime.strptime(start_date, '%d-%m-%Y')
    end_dt = datetime.strptime(end_date, '%d-%m-%Y')
    
    if start_dt > end_dt:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "error_code": "INVALID_DATE_RANGE",
                "message": "Start date must be before or equal to end date",
                "details": f"Start: {start_date}, End: {end_date}"
            }
        )
    
    # Create cache key
    cache_key = f"border_data_{start_date}_{end_date}_{'_'.join(sorted(gates) if gates else ['all'])}"
    
    # Try to get from cache first
    cached_data = cache_manager.get_cached_data(cache_key)
    if cached_data is not None:
        return BorderGatesResponse(
            success=True,
            source="cache",
            total_gates=len(cached_data),
            date_range={"start_date": start_date, "end_date": end_date},
            data=cached_data,
            metadata={
                "cache_duration": f"{CACHE_TIMEOUT_SECONDS} seconds",
                "api_version": "2.0.0",
                "filtered_gates": gates or [],
                **cache_manager.get_cache_stats()
            }
        )
    
    # Fetch fresh data
    raw_data, error = await fetch_border_data(start_date, end_date)
    
    if error:
        logger.error(f"Data fetch error: {error}")
        raise HTTPException(
            status_code=503,
            detail={
                "success": False,
                "error_code": "DATA_SOURCE_ERROR",
                "message": "Unable to fetch data from source",
                "details": error
            }
        )
    
    if not raw_data:
        raise HTTPException(
            status_code=404,
            detail={
                "success": False,
                "error_code": "NO_DATA_FOUND",
                "message": "No border gate data found for the specified date range",
                "details": f"Date range: {start_date} to {end_date}"
            }
        )
    
    # Apply filtering
    filtered_data = apply_border_filter(raw_data, gates)
    
    # Cache the filtered data
    cache_manager.set_cached_data(cache_key, filtered_data)
    
    # Return response
    return BorderGatesResponse(
        success=True,
        source="live",
        total_gates=len(filtered_data),
        date_range={"start_date": start_date, "end_date": end_date},
        data=filtered_data,
        metadata={
            "cache_duration": f"{CACHE_TIMEOUT_SECONDS} seconds",
            "api_version": "2.0.0",
            "filtered_gates": gates or [],
            "data_freshness": "live",
            **cache_manager.get_cache_stats()
        }
    )

@app.get(
    "/health",
    tags=["System"],
    summary="Health Check",
    description="Check API health status and system information"
)
def health_check():
    """System health check endpoint"""
    return {
        "status": "healthy",
        "service": "Border Gates API",
        "version": "2.0.0",
        "timestamp": datetime.now().isoformat(),
        "uptime": "Available 24/7",
        "provider": "JojAPI.com"
    }

@app.get(
    "/cache/stats",
    tags=["System"],
    summary="Cache Statistics",
    description="Get current cache statistics and performance metrics"
)
def get_cache_statistics(cache_manager: CacheManager = Depends(get_cache_manager)):
    """Get cache performance statistics"""
    stats = cache_manager.get_cache_stats()
    return {
        "cache_statistics": stats,
        "timestamp": datetime.now().isoformat(),
        "cache_timeout_seconds": CACHE_TIMEOUT_SECONDS
    }

# Application runner
if __name__ == "__main__":
    uvicorn.run(
        "api_usage:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True
    )
