# PRE Point – rozhraní pro kolegovu část

## Veřejné rozhraní tokenu

```python
from .api import PreTokenProvider, PreAuthError

token_provider: PreTokenProvider = hass.data["pre_point"][entry.entry_id]["api"]
access_token = await token_provider.async_get_access_token()
```

`PREPointApi` implementuje `PreTokenProvider`. Přihlašovací údaje zůstávají v config entry Home Assistantu. Access/refresh token jsou v paměti; po restartu se získají znovu.

## Chování při chybě autentizace

- První selhání přihlášení naplánuje jediný další pokus za 15 minut.
- Před uplynutím 15 minut se žádný další login nepokusí.
- Pokud jediný opakovaný pokus selže, automatické přihlašování se zastaví.
- Integrace vyvolá Home Assistant reauthentication flow. Další přihlášení se provede až po ručním zadání údajů uživatelem.
- Síťové chyby při samotném refresh požadavku jsou hlášeny jako běžná chyba aktualizace; nepovažují se automaticky za selhání hesla/loginu.

## Data

`await api.async_get_sessions()` vrací nezměněnou JSON odpověď z PRE endpointu historie. Aktualizace coordinatoru probíhá každých 15 minut.

## Poznámka

Jde o diagnostickou/custom integraci. Veřejné API stanic, tarify a další senzory nejsou v této verzi implementovány.


## Login failure policy

- If the identity provider explicitly rejects the username/password, the integration immediately requires manual reauthentication. It does not retry those credentials.
- If login fails because of a transient network/server/API error, the integration schedules one retry after 15 minutes.
- If that delayed retry also fails, automatic login is locked until manual reauthentication.
- The distinction relies on explicit identity-provider error text/status. If PRE changes its login page or error wording, credential rejection detection may need updating.
