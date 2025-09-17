import os
import yaml
from typing import Dict, Any, Optional
from src.common.utils import get_logger
import re

logger = get_logger(__name__)

class Config:
    """Manages application configuration with environment variable substitution and overrides."""
    def __init__(self, config_path: str = "src/config/config.yaml"):
        self.config = self._load_config(config_path)
        self.environment = self.get("app.environment", "production")
        self._apply_environment_overrides()
        self._substitute_env_vars()

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load YAML configuration from file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
                logger.info(f"Loaded configuration from {config_path}")
                return config or {}
        except Exception as e:
            logger.error(f"Failed to load config from {config_path}: {e}")
            raise

    def _substitute_env_vars(self):
        """Substitute environment variables in the configuration."""
        def replace_env_vars(obj: Any) -> Any:
            if isinstance(obj, str):
                # Match ${VAR:default} or ${VAR}
                matches = re.findall(r'\${([^}]+)}', obj)
                for match in matches:
                    var_name, default = (match.split(':', 1) + [None])[:2] if ':' in match else (match, None)
                    value = os.getenv(var_name, default)
                    if value is None:
                        logger.warning(f"Environment variable {var_name} not set and no default provided")
                    obj = obj.replace(f"${{{match}}}", value if value is not None else "")
                return obj
            elif isinstance(obj, dict):
                return {k: replace_env_vars(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_env_vars(item) for item in obj]
            return obj

        self.config = replace_env_vars(self.config)

    def _apply_environment_overrides(self):
        """Apply environment-specific configuration overrides."""
        env_overrides = self.config.get("environments", {}).get(self.environment, {})
        def merge_dicts(base: Dict, override: Dict) -> Dict:
            result = base.copy()
            for key, value in override.items():
                if isinstance(value, dict) and key in result and isinstance(result[key], dict):
                    result[key] = merge_dicts(result[key], value)
                else:
                    result[key] = value
            return result
        self.config = merge_dicts(self.config, env_overrides)
        logger.info(f"Applied {self.environment} environment overrides")

    def get(self, key: str, default: Optional[Any] = None) -> Any:
        """Retrieve a configuration value by dotted key path."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            try:
                value = value[k]
            except (KeyError, TypeError):
                logger.debug(f"Config key {key} not found, returning default: {default}")
                return default
        return value

def get_config() -> Config:
    """Dependency injection for Config."""
    return Config()