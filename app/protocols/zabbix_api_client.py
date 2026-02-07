"""
Zabbix API Client — REST клиент для Zabbix.

Модуль для получения данных мониторинга из Zabbix API.
Поддерживает аутентификацию, получение статуса хостов и item'ов.

Использование:
    client = ZabbixAPIClient(url="http://192.168.2.240/api_jsonrpc.php", token="xxx")
    host = await client.get_host("Projector_X")
    items = await client.get_host_items("Projector_X")
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

import structlog
import httpx

logger = structlog.get_logger()


@dataclass
class ZabbixHost:
    """Информация о хосте Zabbix."""
    hostid: str
    host: str
    name: str
    status: int  # 0 = enabled, 1 = disabled
    available: int  # 1 = available, 2 = unavailable
    error: Optional[str] = None
    
    @property
    def is_enabled(self) -> bool:
        return self.status == 0
    
    @property
    def is_available(self) -> bool:
        return self.available == 1


@dataclass
class ZabbixItem:
    """Item из Zabbix."""
    itemid: str
    hostid: str
    key_: str
    name: str
    lastvalue: str
    lastclock: int  # Unix timestamp
    prevvalue: Optional[str] = None
    units: Optional[str] = None
    
    @property
    def last_check_time(self) -> datetime:
        return datetime.fromtimestamp(self.lastclock)


@dataclass 
class ZabbixAPIResult:
    """Результат API вызова."""
    success: bool
    data: Optional[Any]
    error: Optional[str] = None
    error_code: Optional[int] = None
    duration_ms: int = 0


class ZabbixAPIClient:
    """
    REST клиент для Zabbix API.
    
    Использует JSON-RPC 2.0 протокол Zabbix API.
    
    Attributes:
        url: URL Zabbix API (обычно /api_jsonrpc.php)
        token: API токен для аутентификации
        timeout: Таймаут запросов в секундах
    """
    
    def __init__(
        self,
        url: str = "http://192.168.2.240/api_jsonrpc.php",
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 10.0
    ):
        """
        Инициализация клиента.
        
        Args:
            url: URL Zabbix API
            token: API токен (для Zabbix 5.4+)
            username: Логин (для старых версий)
            password: Пароль (для старых версий)
            timeout: Таймаут запросов
        """
        self.url = url
        self.token = token
        self.username = username
        self.password = password
        self.timeout = timeout
        self._auth_token: Optional[str] = None
        self._request_id = 0
    
    def _next_id(self) -> int:
        """Получить следующий ID запроса."""
        self._request_id += 1
        return self._request_id
    
    async def _request(
        self,
        method: str,
        params: Optional[Dict] = None,
        require_auth: bool = True
    ) -> ZabbixAPIResult:
        """
        Выполнить JSON-RPC запрос к Zabbix API.
        
        Args:
            method: API метод
            params: Параметры метода
            require_auth: Требуется ли авторизация
            
        Returns:
            ZabbixAPIResult
        """
        import time
        start_time = time.time()
        
        # Формируем запрос
        request_body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._next_id()
        }
        
        # Добавляем auth
        if require_auth:
            if self.token:
                # API токен (Zabbix 5.4+)
                pass  # Добавится в headers
            elif self._auth_token:
                request_body["auth"] = self._auth_token
        
        headers = {
            "Content-Type": "application/json-rpc"
        }
        
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.url,
                    json=request_body,
                    headers=headers
                )
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            if response.status_code != 200:
                return ZabbixAPIResult(
                    success=False,
                    data=None,
                    error=f"HTTP {response.status_code}",
                    duration_ms=duration_ms
                )
            
            result = response.json()
            
            # Проверяем на ошибку
            if "error" in result:
                error = result["error"]
                return ZabbixAPIResult(
                    success=False,
                    data=None,
                    error=error.get("message", str(error)) + ": " + error.get("data", ""),
                    error_code=error.get("code"),
                    duration_ms=duration_ms
                )
            
            return ZabbixAPIResult(
                success=True,
                data=result.get("result"),
                duration_ms=duration_ms
            )
            
        except httpx.TimeoutException:
            duration_ms = int((time.time() - start_time) * 1000)
            return ZabbixAPIResult(
                success=False,
                data=None,
                error="Request timeout",
                duration_ms=duration_ms
            )
            
        except httpx.ConnectError:
            duration_ms = int((time.time() - start_time) * 1000)
            return ZabbixAPIResult(
                success=False,
                data=None,
                error="Connection failed",
                duration_ms=duration_ms
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("zabbix_api_error", method=method, error=str(e))
            return ZabbixAPIResult(
                success=False,
                data=None,
                error=str(e),
                duration_ms=duration_ms
            )
    
    async def login(self) -> bool:
        """
        Авторизоваться в Zabbix (для версий без API токена).
        
        Returns:
            True если авторизация успешна
        """
        if self.token:
            # Используем API токен, логин не нужен
            return True
        
        if not self.username or not self.password:
            logger.error("zabbix_login_failed", reason="no_credentials")
            return False
        
        result = await self._request(
            method="user.login",
            params={
                "user": self.username,
                "password": self.password
            },
            require_auth=False
        )
        
        if result.success and result.data:
            self._auth_token = result.data
            logger.info("zabbix_login_success", user=self.username)
            return True
        
        logger.error("zabbix_login_failed", error=result.error)
        return False
    
    async def get_host(
        self,
        host_name: str
    ) -> Optional[ZabbixHost]:
        """
        Получить информацию о хосте по имени.
        
        Args:
            host_name: Имя хоста в Zabbix
            
        Returns:
            ZabbixHost или None
        """
        result = await self._request(
            method="host.get",
            params={
                "filter": {"host": host_name},
                "output": ["hostid", "host", "name", "status", "available", "error"]
            }
        )
        
        if not result.success or not result.data:
            logger.warning(
                "zabbix_host_not_found",
                host_name=host_name,
                error=result.error
            )
            return None
        
        host_data = result.data[0] if result.data else None
        if not host_data:
            return None
        
        return ZabbixHost(
            hostid=host_data["hostid"],
            host=host_data["host"],
            name=host_data.get("name", host_data["host"]),
            status=int(host_data.get("status", 0)),
            available=int(host_data.get("available", 0)),
            error=host_data.get("error")
        )
    
    async def get_host_items(
        self,
        host_name: str,
        keys: Optional[List[str]] = None
    ) -> List[ZabbixItem]:
        """
        Получить items хоста.
        
        Args:
            host_name: Имя хоста
            keys: Фильтр по ключам (опционально)
            
        Returns:
            Список ZabbixItem
        """
        params = {
            "host": host_name,
            "output": [
                "itemid", "hostid", "key_", "name",
                "lastvalue", "lastclock", "prevvalue", "units"
            ],
            "sortfield": "name"
        }
        
        if keys:
            params["filter"] = {"key_": keys}
        
        result = await self._request(
            method="item.get",
            params=params
        )
        
        if not result.success or not result.data:
            logger.warning(
                "zabbix_items_not_found",
                host_name=host_name,
                error=result.error
            )
            return []
        
        items = []
        for item_data in result.data:
            items.append(ZabbixItem(
                itemid=item_data["itemid"],
                hostid=item_data["hostid"],
                key_=item_data["key_"],
                name=item_data["name"],
                lastvalue=item_data.get("lastvalue", ""),
                lastclock=int(item_data.get("lastclock", 0)),
                prevvalue=item_data.get("prevvalue"),
                units=item_data.get("units")
            ))
        
        return items
    
    async def get_host_status(
        self,
        host_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить полный статус хоста (хост + items).
        
        Args:
            host_name: Имя хоста
            
        Returns:
            Словарь с host и items
        """
        # Получаем хост
        host = await self.get_host(host_name)
        if not host:
            return None
        
        # Получаем items
        items = await self.get_host_items(host_name)
        
        return {
            "host": {
                "id": host.hostid,
                "name": host.name,
                "enabled": host.is_enabled,
                "available": host.is_available,
                "error": host.error
            },
            "items": [
                {
                    "key": item.key_,
                    "name": item.name,
                    "value": item.lastvalue,
                    "units": item.units,
                    "last_check": item.last_check_time.isoformat()
                }
                for item in items
            ]
        }
    
    async def get_hosts_by_group(
        self,
        group_name: str
    ) -> List[ZabbixHost]:
        """
        Получить все хосты в группе.
        
        Args:
            group_name: Имя группы хостов
            
        Returns:
            Список ZabbixHost
        """
        # Сначала получаем ID группы
        group_result = await self._request(
            method="hostgroup.get",
            params={
                "filter": {"name": group_name},
                "output": ["groupid"]
            }
        )
        
        if not group_result.success or not group_result.data:
            return []
        
        group_id = group_result.data[0]["groupid"]
        
        # Получаем хосты группы
        result = await self._request(
            method="host.get",
            params={
                "groupids": [group_id],
                "output": ["hostid", "host", "name", "status", "available", "error"]
            }
        )
        
        if not result.success or not result.data:
            return []
        
        hosts = []
        for host_data in result.data:
            hosts.append(ZabbixHost(
                hostid=host_data["hostid"],
                host=host_data["host"],
                name=host_data.get("name", host_data["host"]),
                status=int(host_data.get("status", 0)),
                available=int(host_data.get("available", 0)),
                error=host_data.get("error")
            ))
        
        return hosts
    
    async def test_connection(self) -> bool:
        """
        Проверить подключение к Zabbix API.
        
        Returns:
            True если API доступен
        """
        result = await self._request(
            method="apiinfo.version",
            params={},
            require_auth=False
        )
        
        if result.success:
            logger.info("zabbix_connection_ok", version=result.data)
            return True
        
        logger.error("zabbix_connection_failed", error=result.error)
        return False


# Пример использования:
if __name__ == "__main__":
    import asyncio
    
    async def main():
        # Создаём клиент с API токеном
        client = ZabbixAPIClient(
            url="http://192.168.2.240/api_jsonrpc.php",
            token="your-api-token-here",  # Заменить на реальный токен
            timeout=10.0
        )
        
        # Проверяем подключение
        print("Проверка подключения к Zabbix API...")
        connected = await client.test_connection()
        print(f"Подключение: {'OK' if connected else 'FAILED'}")
        
        if connected:
            # Получаем информацию о хосте
            print("\nПолучение статуса хоста 'Projector_X'...")
            status = await client.get_host_status("Projector_X")
            
            if status:
                print(f"\nХост: {status['host']['name']}")
                print(f"Доступен: {status['host']['available']}")
                print(f"Items: {len(status['items'])}")
                
                for item in status["items"][:5]:  # Первые 5 items
                    print(f"  - {item['name']}: {item['value']} {item['units'] or ''}")
            else:
                print("Хост не найден")
    
    asyncio.run(main())
