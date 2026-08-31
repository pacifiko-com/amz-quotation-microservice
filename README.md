# Quotation Microservice

Microservicio en AWS Lambda para centralizar la cotización de productos de Amazon por país. Fase inicial: solo estructura.

Países previstos: Guatemala (`GT`) y Costa Rica (`CR`). La lógica, los contratos (DTOs) y los métodos de Strategy aún no están definidos.

## Arquitectura

Capas + Strategy por país. El handler no conoce reglas locales; cuando existan, las resolverá un factory.

```
quotation_main.py
        │
        ▼
service/                         (vacío)
        │
        ▼
country/country_strategy_factory.py
        │
        ├── country/GT/strategy.py ──► GT/service ──► GT/repository
        └── country/CR/strategy.py ──► CR/service ──► CR/repository
                                              │
                    repository/              (vacío)
                                              │
                                    database/connection.py ──► MySQL
```

| Capa | Rol |
| --- | --- |
| `quotation_main.py` | Entrada Lambda |
| `service/` | Lógica de negocio compartida |
| `repository/` | Acceso a datos compartido |
| `DTO/` | Contratos de entrada/salida entre capas |
| `country/` | Strategy por país (contrato común, aún sin métodos) |
| `country/*/service/` | Lógica de negocio del país |
| `country/*/repository/` | Acceso a datos del país |
| `database/` | Conexión MySQL (warm start) |
| `config/` | Variables de entorno |
| `Utils/` | Utilidades básicas (logger) |

Convenciones previstas (cuando se implemente):

- Service y repository se hablan con DTOs, no con entidades ni listas largas de parámetros.
- Si hay pocos valores sueltos, máximo 3 parámetros primitivos.
- Las Strategy de cada país deben ser intercambiables sobre el mismo contrato.

## Estructura

```
quotation-microservice/
├── quotation_main.py
├── requirements.txt
├── config/
│   └── settings.py
├── database/
│   └── connection.py
├── Utils/
│   └── logger.py
├── DTO/
├── service/
├── repository/
└── country/
    ├── country_strategy_abstract.py
    ├── country_strategy_factory.py
    ├── GT/
    │   ├── strategy.py
    │   ├── service/
    │   └── repository/
    └── CR/
        ├── strategy.py
        ├── service/
        └── repository/
```

## Lambda

- Handler: `quotation_main.lambda_handler`
- Runtime recomendado: Python 3.11+
- Variables: ver `.env.example`

## Cómo agregar un país (más adelante)

1. Crear `country/XX/` con `strategy.py`, `service/` y `repository/`.
2. Implementar el contrato de `CountryQuotationStrategy` cuando se defina.
3. Registrar el país en el factory.
