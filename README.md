# Quotation Microservice

AWS Lambda que centraliza la cotización de productos de Amazon para Guatemala
(`GT`) y Costa Rica (`CR`). El caller consulta Amazon y envía `OFFERS`; este
servicio no consume ninguna API de Amazon.

Una sola Lambda atiende ambos países. El campo `country` selecciona la Strategy
y la conexión MySQL correspondiente (`DB_GT_*` o `DB_CR_*`).

## Arquitectura

```text
quotation_main.py
  └── QuotationService
        └── CountryStrategyFactory
              ├── GuatemalaQuotationStrategy
              │     ├── GuatemalaQuotationService
              │     └── GuatemalaQuotationRepository
              └── CostaRicaQuotationStrategy
                    ├── CostaRicaQuotationService
                    └── CostaRicaQuotationRepository
                              └── database/connection.py
                                    ├── GT → MySQL GT
                                    └── CR → MySQL CR
```

- `quotation_main.py`: adapta el evento Lambda al contrato.
- `DTO/`: contratos entre handler, services y repositories.
- `service/`: orquestación común y adaptación de ofertas Amazon.
- `country/*/service/`: únicamente calculadora y promesa de cada país.
- `country/*/repository/`: consultas específicas del esquema del país.
- `repository/`: consultas compartidas por esquemas GT/CR.
- `database/`: conexión MySQL y `oc_setting` globales, cargados fuera del handler y reutilizados en warm starts.
- `config/`: configuración del despliegue, no reglas de negocio.

`QuotationService` contiene el flujo completo común, basado en el cotizador GT.
La Strategy no cotiza un producto completo: expone operaciones puntuales para
UNSPSC/defaults, `oc_product`, partida, courier de categorías, tasa de cambio,
`oc_setting`, calculadora y promesa. El orquestador no contiene condiciones
`GT`/`CR`.

## Flujo

Para cada producto, en el orden recibido:

1. Cargar únicamente las constantes requeridas de `oc_setting`.
2. Leer peso, courier y partida de `oc_product`.
3. Aplicar overrides del request.
4. Resolver UNSPSC en `oc_arancel_amz`.
5. Si no existe, registrar el código con `INSERT IGNORE` en
   `oc_category_amz_new` y usar defaults de `oc_setting`.
6. Resolver la partida en `oc_partida_arancelaria`.
7. Aplicar la precedencia común de courier/arancel/restricción y, cuando
   corresponda, consultar courier en el árbol `oc_category`.
8. Seleccionar una oferta desde `amz_offers`: primera elegible con
   `deliveryRange.max`; si el request activa `prefer_amazon_fulfillment`,
   primera elegible AF; si no, primera elegible. El shipping de las pasadas
   sin max es `shippingOptions[0]`.
9. Si la oferta elegida trae buying guidance restringido, abortar el producto.
   Si no, calcular oferta y, cuando exista list price, lista; el special se
   decide comparando los precios locales. Luego resolver la promesa.
10. Retornar un `ResultObject`; un error no detiene los demás productos.

La orquestación canónica proviene del cotizador activo GT y del diagrama,
corregido con el comportamiento activo. No se copiaron ramas muertas ni
divergencias del ETL. GT y CR mantienen mediante Strategy únicamente sus
diferencias de esquema, partida, tasa, calculadora y promesa.

## Entrada Lambda

```json
{
  "country": "GT",
  "prefer_amazon_fulfillment": false,
  "products": [
    {
      "product_id": 123,
      "amz_weight_kg": 0.72,
      "pac_product_weight": null,
      "pac_product_courier": null,
      "pac_product_partida": "0012.34",
      "unspsc": "52161500",
      "amz_offers": [
        {
          "offerId": "offer-1",
          "productCondition": "NEW",
          "condition": {"conditionValue": "NEW"},
          "price": {"priceType": "NEW", "value": {"amount": 49.99}},
          "listPrice": {"value": {"amount": 59.99}},
          "fulfillmentType": "AMAZON_FULFILLMENT",
          "availability": "In Stock.",
          "buyingGuidance": "",
          "deliveryInformation": "Entrega en 8 días",
          "shippingOptions": [
            {
              "shippingCost": {"value": {"amount": 0}},
              "deliveryRange": {"max": "2026-09-10T23:59:59-04:00"}
            }
          ]
        }
      ]
    }
  ]
}
```

Reglas de forma:

- `country`: requerido; `GT` o `CR`.
- `prefer_amazon_fulfillment`: boolean opcional; default `false`. Si es
  `true`, después de la pasada con `deliveryRange.max` se elige la primera
  oferta elegible `AMAZON_FULFILLMENT` (detail page / variantes GT). Si es
  `false` o se omite, esa pasada no corre (cotizador GT).
- `products`: arreglo no vacío.
- `product_id`: entero requerido.
- `amz_weight_kg`: número no negativo requerido.
- `pac_product_weight`: número no negativo opcional; reemplaza la consulta de
  peso Pacifiko, pero el peso final sigue siendo el mayor entre Amazon/Pacifiko.
- `pac_product_courier`: boolean opcional; reemplaza `oc_product.courier`.
- `pac_product_partida`: string opcional para conservar ceros iniciales.
- `unspsc`: string no vacío, o `null` para usar defaults de país sin registrar el código como desconocido. No se aceptan cadenas vacías ni solo espacios.
- `amz_offers`: arreglo crudo `includedDataTypes.OFFERS`; su forma puede variar.

## Retorno

```json
{
  "success": true,
  "message": "All products were quoted successfully.",
  "result": [
    {
      "success": true,
      "Message": "Product quoted successfully.",
      "product_id": 123,
      "offer_id": "offer-1",
      "amazon_price": 49.99,
      "price_dolar": 59.99,
      "price_local": 529.0,
      "price_without_tax_dolar": 53.56,
      "price_without_tax_local": 473.0,
      "special_price_dolar": 49.99,
      "special_price_local": 449.0,
      "special_price_without_tax_dolar": 44.63,
      "special_price_without_tax_local": 401.0,
      "cost_dolar": 55.22,
      "cost_local": 427.96,
      "exchange_rate": 7.75,
      "currency_code": "GTQ",
      "delivery_promise_amz": 1,
      "courier": false,
      "restriction": 0,
      "partida": "0012.34",
      "quotation_notes": [
        "UNSPSC 52161500 encontrado en oc_arancel_amz (arancel 0.15, courier False, restricción 0, margen 1.2, peligroso False).",
        "Partida arancelaria 0012.34 tomada del override del request.",
        "Producto no es courier; arancel tomado de la partida: 0.05."
      ]
    }
  ]
}
```

- `amazon_price`: oferta Amazon seleccionada en USD, con el shipping resuelto.
- `price_dolar`: list price + shipping; si no hay lista, offer + shipping.
- `special_price_dolar`: offer + shipping si supera el umbral; si no, `0`.
- `cost_dolar`: landed cost calculado en USD.
- `cost_local`: landed cost convertido con `exchange_rate`.
- `price_local`: precio público local (lista si hay special; si no, oferta).
- `price_without_tax_dolar`: precio de venta USD tomado de `calculate()` antes del IVA de venta.
- `price_without_tax_local`: precio de venta local tomado de `calculate()` antes del IVA de venta.
- `special_price_local`: precio local de la oferta si hay special; si no, `null`.
- `special_price_without_tax_dolar`: special USD antes del IVA de venta; si no hay special, `null`.
- `special_price_without_tax_local`: special local antes del IVA de venta; si no hay special, `null`.
- `exchange_rate`: tasa USD → moneda local usada en el cálculo.
- `currency_code`: código ISO de la moneda local (`GTQ` o `CRC`).
- `success` global es `true` solamente si todos los resultados son exitosos.
- Se conserva `Message` con mayúscula dentro de cada resultado por contrato.
- `quotation_notes`: arreglo de textos en cada producto cotizado con éxito. Cada
  nota se escribe en el `if` que tomó la decisión (UNSPSC, partida, courier,
  calculadora, oferta, special, promesa). En fallos el arreglo va vacío.

## Precedencia por país

### Guatemala

- Peso: `amz_weight_kg` se convierte a lb, se compara con Pacifiko (ya en lb)
  y se propaga `weight_lb`. La calculadora no vuelve a convertir.
- Courier base: override/producto no cero; en otro caso UNSPSC.
- Con partida: courier de partida. Si es courier, arancel/restricción permanecen
  desde UNSPSC; si no es courier, la partida puede reemplazarlos.
- Sin partida y courier cero: árbol de categorías.
- Calculadora: flete por libra, seguro en base arancelaria, arancel, IVA de
  importación courier, desaduanaje, mercancía peligrosa, margen e IVA venta.
- Promesa: fecha máxima, días hábiles, `dias_importacion_amz` y rangos de
  `global_store_promises`.

### Costa Rica

- Peso: igual que GT; la política expone `weight_lb` y la calculadora lo usa
  sin reconvertir a kg.
- Courier: override/producto, UNSPSC y árbol de categorías.
- Partida: reemplaza porcentaje por `dai + isc`; no reemplaza courier ni
  restricción.
- Calculadora: CIF, DAI/ISC, IVA aduanas, flete real, combustible, Ley 6946,
  desaduanaje, seguro, trámite courier, margen e IVA venta.
- Promesa: texto Amazon, umbrales AF/MF y ajuste courier.

## Constantes Guatemala

Verificadas contra `lectura-prod-gt`. La tabla enumera todas las keys consumidas
por la implementación; no incluye credenciales ni tokens.

| Key | País | Uso | Estado |
| --- | --- | --- | --- |
| `tipo_de_cambio` | GT | Conversión USD → GTQ | Existente |
| `currency_code` | GT | Código ISO de moneda local | Nueva |
| `margen` | GT | Margen fallback y default UNSPSC | Existente |
| `tarifa_de_flete` | GT | Flete USD/libra no courier | Existente |
| `tarifa_de_flete_courier` | GT | Flete USD/libra courier | Existente |
| `desaduanaje` | GT | Cargo fijo no courier | Existente |
| `courier_desaduanaje` | GT | Cargo fijo courier | Existente |
| `seguro_valor_producto` | GT | Seguro en base arancelaria | Existente |
| `danger_dolar` | GT | Cargo mercancía peligrosa | Existente |
| `dias_importacion_amz` | GT | Buffer de promesa | Existente |
| `global_store_promises` | GT | Rangos/textos de promesa | Existente |
| `kilos_por_libra` | GT | Reservada (el peso ya llega en `weight_lb`) | Existente |
| `default_arancel` | GT | Arancel para UNSPSC desconocido | Nueva |
| `default_restriction` | GT | Restricción default UNSPSC | Nueva |
| `default_arancel_category_cod` | GT | Categoría default UNSPSC | Nueva |
| `default_danger_good_active` | GT | Peligroso default UNSPSC | Nueva |
| `default_courier` | GT | Courier default UNSPSC | Nueva |
| `default_iva_venta` | GT | IVA de venta | Nueva |
| `iva_importacion` | GT | IVA de importación courier | Nueva |
| `special_discount_threshold` | GT | Umbral de special | Nueva |
| `max_product_weight_kg` | GT | Límite de peso | Nueva |
| `price_rounding_step` | GT | Paso de redondeo hacia arriba | Nueva |
| `allowed_offer_price_types` | GT | Tipos de precio Amazon elegibles | Nueva |
| `unavailable_offer_terms` | GT | Textos de oferta no disponible | Nueva |
| `preorder_offer_terms` | GT | Textos de preventa | Nueva |
| `promise_oos_terms` | GT | Textos OOS de promesa | Nueva |
| `promise_in_stock_exact_terms` | GT | Disponibilidad exacta en stock | Nueva |
| `promise_in_stock_contains_terms` | GT | Disponibilidad parcial en stock | Nueva |
| `restricted_guidance_term` | GT | Texto de restricción Amazon | Nueva |
| `amazon_new_condition` | GT | Condición vendible | Nueva |
| `amazon_fulfillment_type` | GT | Identificador AF | Nueva |
| `amazon_fulfillment_free_shipping` | GT | Política shipping AF | Nueva |
| `prefer_offer_with_delivery_range` | GT | Prioridad de oferta con fecha | Nueva |
| `quotation_timezone` | GT | Zona horaria de promesa | Nueva |
| `promise_preorder_tier` | GT | Tier para preventa | Nueva |
| `promise_missing_delivery_tier` | GT | Tier sin fecha válida | Nueva |
| `promise_below_range_tier` | GT | Tier bajo el rango mínimo | Nueva |
| `promise_fallback_tier` | GT | Tier fuera de rangos | Nueva |

## Constantes Costa Rica

Verificadas contra `lectura-qa-cr`.

| Key | País | Uso | Estado |
| --- | --- | --- | --- |
| `tipo_de_cambio` | CR | Conversión USD → CRC | Existente |
| `currency_code` | CR | Código ISO de moneda local | Nueva |
| `margen` | CR | Margen fallback | Existente |
| `default_arancel` | CR | Arancel fallback | Existente |
| `default_iva_venta` | CR | IVA de venta fallback si no hay CABYS | Existente |
| `tax_usa` | CR | Activa impuesto USA | Existente |
| `ley_6946` | CR | Tasa Ley 6946 | Existente |
| `courier_iva_aduanas` | CR | IVA aduanas courier | Existente |
| `poliza_iva_aduanas` | CR | IVA aduanas póliza | Existente |
| `courier_flete_aduana_kg` | CR | Flete CIF courier | Existente |
| `poliza_flete_aduana_kg` | CR | Flete CIF póliza | Existente |
| `courier_flete_kg` | CR | Flete real courier | Existente |
| `poliza_flete_kg` | CR | Flete real póliza | Existente |
| `courier_fee_combustible` | CR | Combustible courier | Existente |
| `poliza_fee_combustible` | CR | Combustible póliza | Existente |
| `courier_desaduanaje` | CR | Desaduanaje courier | Existente |
| `poliza_desaduanaje` | CR | Desaduanaje póliza | Existente |
| `courier_seguro_aduanas` | CR | Seguro CIF courier | Existente |
| `poliza_seguro_aduanas` | CR | Seguro CIF póliza | Existente |
| `courier_seguro_flete` | CR | Seguro flete courier | Existente |
| `poliza_seguro_flete` | CR | Seguro flete póliza | Existente |
| `courier_tramite_permisos` | CR | Fee permisos courier | Existente |
| `tax_usa_rate` | CR | Tasa de impuesto USA | Nueva |
| `default_restriction` | CR | Restricción default UNSPSC | Nueva |
| `default_arancel_category_cod` | CR | Categoría default UNSPSC | Nueva |
| `default_danger_good_active` | CR | Peligroso default UNSPSC | Nueva |
| `default_courier` | CR | Courier default UNSPSC | Nueva |
| `special_discount_threshold` | CR | Umbral de special | Nueva |
| `max_product_weight_kg` | CR | Límite de peso | Nueva |
| `price_rounding_step` | CR | Paso de redondeo hacia arriba | Nueva |
| `allowed_offer_price_types` | CR | Tipos de precio Amazon elegibles | Nueva |
| `unavailable_offer_terms` | CR | Textos de oferta no disponible | Nueva |
| `preorder_offer_terms` | CR | Textos de preventa | Nueva |
| `promise_tomorrow_terms` | CR | Textos equivalentes a mañana | Nueva |
| `promise_tomorrow_days` | CR | Días equivalentes a mañana | Nueva |
| `restricted_guidance_term` | CR | Texto de restricción Amazon | Nueva |
| `amazon_new_condition` | CR | Condición vendible | Nueva |
| `amazon_fulfillment_type` | CR | Identificador AF | Nueva |
| `amazon_fulfillment_free_shipping` | CR | Política shipping AF | Nueva |
| `prefer_offer_with_delivery_range` | CR | Prioridad de oferta con fecha | Nueva |
| `promise_af_tier_1_max_days` | CR | Umbral AF tier 1 | Nueva |
| `promise_af_tier_2_max_days` | CR | Umbral AF tier 2 | Nueva |
| `promise_af_tier_3_max_days` | CR | Umbral AF tier 3 | Nueva |
| `promise_mf_tier_1_max_days` | CR | Umbral MF tier 1 | Nueva |
| `promise_mf_tier_2_max_days` | CR | Umbral MF tier 2 | Nueva |
| `promise_mf_tier_3_max_days` | CR | Umbral MF tier 3 | Nueva |
| `promise_missing_delivery_af_tier` | CR | Tier AF sin días | Nueva |
| `promise_missing_delivery_mf_tier` | CR | Tier MF sin días | Nueva |
| `courier_promise_shift` | CR | Penalización de promesa courier | Nueva |
| `promise_preorder_tier` | CR | Tier para preventa | Nueva |
| `promise_tier_1_value` | CR | Valor de tier corto | Nueva |
| `promise_tier_2_value` | CR | Valor de tier medio | Nueva |
| `promise_tier_3_value` | CR | Valor de tier largo | Nueva |
| `promise_fallback_tier` | CR | Tier fuera de rangos | Nueva |

Los scripts [GT_new_quotation_settings.sql](database/seeds/GT_new_quotation_settings.sql)
y [CR_new_quotation_settings.sql](database/seeds/CR_new_quotation_settings.sql)
contienen únicamente keys nuevas. Deben revisarse y ejecutarse una sola vez;
el servicio falla de forma controlada si falta una key requerida.

## Configuración y ejecución

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m unittest discover -s tests -v
```

Variables de entorno:

- `DB_GT_HOST`, `DB_GT_PORT`, `DB_GT_NAME`, `DB_GT_USER`,
  `DB_GT_PASSWORD`, `DB_GT_CONNECT_TIMEOUT`.
- `DB_CR_HOST`, `DB_CR_PORT`, `DB_CR_NAME`, `DB_CR_USER`,
  `DB_CR_PASSWORD`, `DB_CR_CONNECT_TIMEOUT`.
- `LOG_LEVEL`.
- `OC_SETTING_CACHE_TTL_SECONDS` (segundos de reutilización de
  `oc_setting` en memoria; default `300`. `0` recarga en cada llamada).
- `SECRETS_MANAGER_SECRET_ARN` (solo en Lambda; el JSON del secreto debe
  incluir las keys `DB_GT_*` y `DB_CR_*`).

Handler AWS: `quotation_main.lambda_handler`.

## Infraestructura AWS

La infraestructura se define en
[`sam/template-quotation.yaml`](sam/template-quotation.yaml) y se despliega
desde la raíz del repositorio. QA y producción son stacks independientes: cada
`sam deploy` crea solo las dos funciones de ese ambiente.

| Ambiente | Handler | Función |
| --- | --- | --- |
| QA | `quotation_main.lambda_handler` | `amz-quotation-microservice-qa-quotation` |
| QA | `price_quotation_main.lambda_handler` | `amz-quotation-microservice-qa-price-quotation` |
| Producción | `quotation_main.lambda_handler` | `amz-quotation-microservice-prod-quotation` |
| Producción | `price_quotation_main.lambda_handler` | `amz-quotation-microservice-prod-price-quotation` |

Cada stack crea su propia API REST (`amz-quotation-microservice-qa-api` o
`amz-quotation-microservice-prod-api`), API Key y Usage Plan. El stage de API
Gateway es el ambiente, para que la URL deje claro el destino:

- QA: `POST /qa/quotation` y `POST /qa/price-quotation`.
- Producción: `POST /prod/quotation` y `POST /prod/price-quotation`.

Todas las funciones usan Python 3.12, las mismas subnets privadas y el mismo
security group configurados en `categories-bulk-functions`:
`subnet-0d4a8910e3afcfd71`, `subnet-0a200336479c88ead` y
`sg-0eb223bab8ae85751`. SAM crea el IAM Role de cada función (sin nombre fijo)
con ejecución básica, acceso a la VPC y `secretsmanager:GetSecretValue`
únicamente sobre el secreto de ese ambiente.

`SecretArn` identifica el secreto de Secrets Manager. El valor debe ser un
JSON con las keys `DB_GT_*` y `DB_CR_*` (y opcionalmente `LOG_LEVEL` y
`OC_SETTING_CACHE_TTL_SECONDS`). Al
iniciar, `config/secrets_loader.py` copia esas keys al entorno y
`config/settings.py` las lee. El runtime de Lambda ya incluye boto3. Las
Lambdas están en subnets privadas: Secrets Manager requiere NAT o un VPC
endpoint. No uses los secretos de `categories-bulk-functions` salvo que
contengan exactamente la configuración de quotation.

La configuración de SAM está en
[`samconfig.toml`](samconfig.toml). Debes desplegar con `--config-env qa` o
`--config-env prod`. No guardes contraseñas ni valores secretos en el
repositorio.

Ejemplo de secreto JSON:

```json
{
  "DB_GT_HOST": "gt.example.internal",
  "DB_GT_PORT": "3306",
  "DB_GT_NAME": "qa_gt",
  "DB_GT_USER": "quotation",
  "DB_GT_PASSWORD": "...",
  "DB_GT_CONNECT_TIMEOUT": "10",
  "DB_CR_HOST": "cr.example.internal",
  "DB_CR_PORT": "3306",
  "DB_CR_NAME": "qa_cr",
  "DB_CR_USER": "quotation",
  "DB_CR_PASSWORD": "...",
  "DB_CR_CONNECT_TIMEOUT": "10",
  "OC_SETTING_CACHE_TTL_SECONDS": "300"
}
```

### Despliegue en QA

Desde `C:\xampp\htdocs\lambdas\quotation-microservice`:

```bash
sam build --config-env qa
sam deploy --config-env qa
```

### Despliegue en producción

Ejecuta primero un build nuevo para evitar desplegar artefactos construidos para
otro ambiente:

```bash
sam build --config-env prod
sam deploy --config-env prod
```

Ambos comandos utilizan la región `us-east-2`, `CAPABILITY_IAM`, un bucket
administrado por SAM y el template generado en `.aws-sam/build/template.yaml`.

### API Keys y outputs

Después del despliegue, SAM muestra las URLs de ese ambiente, el ID de la API
Key, el Usage Plan y los nombres de las dos funciones. Para obtener el valor
de una API Key:

```bash
aws apigateway get-api-key \
  --api-key <API_KEY_ID> \
  --include-value \
  --region us-east-2
```

El cliente debe enviar la clave en el header `x-api-key`.

### Validación y eliminación

Valida la plantilla antes de desplegar:

```bash
sam validate --lint --template-file sam/template-quotation.yaml
```

Para eliminar un ambiente completo, incluyendo sus Lambdas, API Gateway, API
Key, Usage Plan e IAM Role:

```bash
sam delete --stack-name amz-quotation-microservice-qa --region us-east-2
sam delete --stack-name amz-quotation-microservice-prod --region us-east-2
```

Ejecuta únicamente el comando del ambiente que deseas eliminar y revisa el
nombre real del stack antes de ejecutarlo.

## Extender a otro país

1. Crear `country/XX/strategy.py`, `service/` y `repository/`.
2. Implementar todas las operaciones puntuales de
   `CountryQuotationStrategy`; no agregar un flujo completo por país.
3. Registrar la Strategy en el factory.
4. Documentar y provisionar todas las keys `oc_setting`.
5. Agregar golden tests de política, cálculo y promesa.
