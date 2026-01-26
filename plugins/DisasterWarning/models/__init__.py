"""
数据模型模块
"""

from .models import (
    DisasterType,
    DataSource,
    DATA_SOURCE_MAPPING,
    get_data_source_from_id,
    EarthquakeData,
    TsunamiData,
    WeatherAlarmData,
    DisasterEvent,
    create_earthquake_data,
    validate_earthquake_data,
    convert_old_model,
)

from .data_source_config import (
    DataSourceConfig,
    DATA_SOURCE_CONFIGS,
    get_data_source_config,
    get_intensity_based_sources,
    get_scale_based_sources,
)

__all__ = [
    # models.py
    "DisasterType",
    "DataSource",
    "DATA_SOURCE_MAPPING",
    "get_data_source_from_id",
    "EarthquakeData",
    "TsunamiData",
    "WeatherAlarmData",
    "DisasterEvent",
    "create_earthquake_data",
    "validate_earthquake_data",
    "convert_old_model",
    # data_source_config.py
    "DataSourceConfig",
    "DATA_SOURCE_CONFIGS",
    "get_data_source_config",
    "get_intensity_based_sources",
    "get_scale_based_sources",
]


