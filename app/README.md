# Ocean Control System

Система автоматизации управления оборудованием Приморского Океанариума.

## Установка

```bash
# Создать виртуальное окружение
python -m venv venv
.\venv\Scripts\activate

# Установить зависимости
pip install -r requirements.txt

# Запустить сервер
python main.py
```

## Использование

Откройте `http://localhost:8000` в браузере.

## Структура

```
ocean_control/
├── main.py           # Точка входа
├── config.json       # Конфигурация устройств
├── core/             # Базовые модули
├── protocols/        # Протоколы устройств
├── services/         # Бизнес-логика
├── api/              # REST API
├── db/               # База данных
└── static/           # Веб-интерфейс
```
