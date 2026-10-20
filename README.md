# PRE Point pro Home Assistant

[![Podpoř mě na Ko-fi](https://img.shields.io/badge/Ko--fi-kup%20mi%20kafe-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/parzival59520)

🇬🇧 **English:** [see below](#english).

Neoficiální integrace nabíjecí sítě **PRE (PRE Point / aplikace PRE CHARGE)** do
Home Assistantu:

- **stav nabíjecích stanic** z veřejné mapy chargepre.smatrics.com (bez přihlášení),
- **historie nabíjení** na tvém účtu, s **cenou spočítanou podle tvého tarifu**
  (PRE ceny v aplikaci nezobrazuje),
- **dlouhodobé statistiky** nabité energie a útraty pro grafy po měsících.

> Integrace není produktem PRE ani SMATRICS. Používá nedokumentovaná rozhraní, která
> se mohou kdykoli změnit. Je jen pro čtení: nic nespouští, nezastavuje ani nerezervuje.

## Co přidá

Účet (zařízení „PRE Point“):

| Entita | Popis |
|---|---|
| Poslední nabíjení | kWh posledního nabíjení; v atributech začátek, délka, stojan, adresa, typ, výkon, tarif a cena za kWh |
| Poslední nabíjení – cena, – začátek | cena podle tarifu, čas začátku |
| Energie tento měsíc, Útrata tento měsíc | součet nabíjení zahájených v tomto měsíci (útrata včetně měsíčního poplatku) |
| Nabíjecí relace | počet nabíjení v archivu |

Každá sledovaná lokalita (zařízení): počet volných stojanů a stav každého stojanu
(volný, nabíjí, rezervovaný, blokovaný, mimo provoz…); v atributech typ, výkon,
konektory a cena za kWh podle tvého tarifu.

Statistiky: `pre_point:charged_energy` (kWh) a `pre_point:charging_cost` (Kč).

## Ceny

PRE ceny nezobrazuje (chodí až ve vyúčtování), proto je integrace počítá sama.
Při nastavení zadáš tarif (výchozí PRE Point Start):

| Pásmo | Výchozí cena |
|---|---|
| AC (do 49 kW) | 9 Kč/kWh |
| DC (do 149 kW) | 12 Kč/kWh |
| UFC (nad 149 kW) | 14 Kč/kWh |
| měsíční poplatek | 0 Kč |

Pásmo se určí podle typu a výkonu stojanu, kde se nabíjelo. **Cena se nabíjení
přiřadí, když se poprvé objeví, a zůstane mu.** Změna tarifu v možnostech integrace
tak platí pro nová nabíjení; zaškrtnutím „Přepočítat i dřívější nabíjení“ se
přepočítá všechno. Měsíční poplatek se připočítá ke každému měsíci od prvního
nabíjení.

## Upozornění a odpovědnost

- Integrace je **neoficiální**. Nemá žádnou vazbu na PRE ani SMATRICS, nikdo z nich ji nevytvořil,
  neschválil ani nepodporuje. Používá nedokumentovaná rozhraní, která se mohou kdykoli
  změnit nebo přestat fungovat.
- Používáš ji **výhradně na vlastní riziko a odpovědnost**. Autor neodpovídá za žádné škody
  ani důsledky jejího použití, zejména za **omezení nebo zablokování účtu**, chybné nebo
  chybějící údaje (např. o ceně nebo stavu nabíjení) ani za porušení podmínek služby.
- Totéž platí pro **jakékoli úpravy kódu nebo nastavení**, hlavně pro zkrácení intervalu
  dotazů pod výchozí hodnoty. Tím zvyšuješ zátěž serverů provozovatele i riziko zablokování
  a odpovídáš za to sám.
- Před použitím si přečti podmínky služby PRE Point / PRE CHARGE a sám posuď, jestli je s nimi
  používání integrace v souladu.
- Software je poskytován „tak, jak je“, bez jakékoli záruky (viz licence MIT).

## Instalace

### HACS

Nejrychleji tlačítkem (otevře HACS ve tvém Home Assistantu):

[![Otevřít v HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ParzivalX78&repository=ha-pre-point&category=integration)

Nebo ručně:

1. HACS → tři tečky → **Vlastní repozitáře**.
2. Přidej URL tohoto repozitáře, kategorie **Integrace**.
3. Nainstaluj „PRE Point“ a restartuj Home Assistant.

### Ručně

Zkopíruj `custom_components/pre_point` do `config/custom_components/` a restartuj
Home Assistant.

## Nastavení

**Nastavení → Zařízení a služby → Přidat integraci → PRE Point:**

1. e-mail a heslo k účtu PRE Point,
2. tarif,
3. stanice ke sledování: předvybraná jsou místa, kde jsi nabíjel; pod nimi stanice
   PRE v okolí domova (okruh a filtr jde změnit a hledat znovu).

## Jak to funguje a jak šetrně se ptá

- **Stanice:** veřejná mapa, jeden dotaz na stav všech sledovaných stojanů za cyklus
  (výchozí 10 minut, 5–60 min). Klíč, který web posílá každému návštěvníkovi,
  integrace čte ze stránky (v kódu není). Podrobnosti lokalit jednou denně.
- **Historie:** jednou za hodinu. Nabíjení se ukládají do archivu v `.storage`
  (private), takže statistiky drží i nabíjení starší než 12 měsíců, které API vrací.
- **Zpomalení při potížích:** po chybě nebo HTTP 429 se interval zdvojnásobí
  (respektuje `Retry-After`), nejvýš na hodinu.
- Stanice a historie jsou na sobě nezávislé: potíže s přihlášením neshodí stav
  stanic a výpadek mapy nesahá na účet.

### Přihlášení a pravidla při selhání (`auth.py`)

Přihlášení je v samostatném `auth.py`. Ostatní části o přihlašování nic nevědí,
o token si řeknou přes rozhraní:

```python
from .api import PreTokenProvider, PreAuthError

token_provider: PreTokenProvider = entry.runtime_data.api
access_token = await token_provider.async_get_access_token()
```

- Server odmítne e-mail nebo heslo → hned se vyžádá ruční přihlášení, bez opakování.
- Přechodná chyba sítě nebo serveru → jeden další pokus za 15 minut.
- Selže i ten → automatické přihlašování se zastaví a HA požádá o nové zadání hesla.
- Při dočasném problému zůstane integrace načtená (stanice běží dál), aby HA
  opakovaným spouštěním nezkracoval tu patnáctiminutovou pauzu.

**Uložený token:** obnovovací token (platí 60 dní) se ukládá přes
`Store(..., private=True)` do `.storage/pre_point_<entry_id>_auth`, takže restart
ani změna voleb nedělá nové přihlášení heslem. Access token zůstává jen v paměti.
Když PRE uložený token odmítne, smaže se a platí běžná pravidla přihlášení.
Test přihlášení v nastavení běží s `entry_id=None` a nic neukládá; při odebrání
integrace se uložený token smaže.

**Logy** neobsahují text výjimek ani adresy přesměrování (mohly by nést OAuth
`code=`), jen obecné zprávy a typ chyby.

## Grafy

Na nástěnku (**Upravit → Přidat kartu → Ruční**):

```yaml
type: vertical-stack
cards:
  - type: horizontal-stack
    cards:
      - type: statistic
        name: PRE nabito tento měsíc
        entity: pre_point:charged_energy
        stat_type: change
        period:
          calendar:
            period: month
      - type: statistic
        name: PRE útrata tento měsíc
        entity: pre_point:charging_cost
        stat_type: change
        period:
          calendar:
            period: month
  - type: statistics-graph
    title: PRE po měsících
    entities:
      - entity: pre_point:charged_energy
        color: "#f4bd4a"
    stat_types: [change]
    period: month
    chart_type: bar
    days_to_show: 365
```

Žlutá `#f4bd4a` odpovídá druhé barvě palety Home Assistantu; integrace E.ON Drive
používá modrou `#4269d0`. Ve společném grafu stačí dát obě statistiky do `entities`
s těmito barvami:

```yaml
type: statistics-graph
title: Nabitá energie – E.ON a PRE
entities:
  - entity: eon_drive:charged_energy
    color: "#4269d0"
  - entity: pre_point:charged_energy
    color: "#f4bd4a"
stat_types: [change]
period: month
chart_type: bar
days_to_show: 365
```

## Vývoj

```bash
python3.13 -m venv .venv && . .venv/bin/activate
pip install pytest-homeassistant-custom-component
pytest
```

Testy používají jen smyšlená data a přihlášení nahrazují atrapou. **Záznamy provozu
(mitmproxy apod.) do repozitáře nikdy nedávej**, obsahují heslo a tokeny.

## Podpora

Integrace je zdarma a open source. Jestli ti šetří čas nebo peníze, můžeš mi
koupit kafe ☕

[![Podpoř mě na Ko-fi](https://img.shields.io/badge/Ko--fi-kup%20mi%20kafe-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/parzival59520)

Chyby a nápady piš do [Issues](../../issues).

## Licence

MIT, viz [LICENSE](LICENSE).

---

## English

Unofficial Home Assistant integration for the **PRE Point (PRE CHARGE app)** EV charging
network in the Czech Republic. It is **read-only**: it never starts, stops or reserves charging.

### What you get

- **Charging station status** from the public map chargepre.smatrics.com (no sign-in):
  number of free chargers and the state of each charger, with type, power, connectors and
  your price per kWh in the attributes.
- **Charging history of your account** with the **price calculated from your tariff**
  (PRE does not show prices in the app): last session, energy and cost this month
  (incl. the monthly fee), number of sessions.
- **Long-term statistics** `pre_point:charged_energy` (kWh) and `pre_point:charging_cost`
  (CZK) for monthly charts. The YAML for the charts is in the Czech part above.

### Installation

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ParzivalX78&repository=ha-pre-point&category=integration)

Or HACS → ⋮ → **Custom repositories** → add this repository's URL, category **Integration**,
install „PRE Point“ and restart Home Assistant. Manual install: copy
`custom_components/pre_point` to `config/custom_components/` and restart.

### Setup

**Settings → Devices & services → Add integration → PRE Point:**

1. e-mail and password of your PRE Point account,
2. your tariff (default PRE Point Start: AC 9, DC 12, UFC 14 CZK/kWh, monthly fee 0),
3. stations to follow (places where you charged are preselected, PRE stations near home
   are listed below them).

Update interval: 10 minutes by default, 5–60 minutes allowed.

### Gentle on the server

One request per cycle for all followed stations, history once an hour, the refresh token is
stored and reused, and the interval doubles (up to one hour, honouring `Retry-After`) after
errors or HTTP 429. Station status and account history are independent of each other.

### Disclaimer

- This integration is **unofficial**. It is not affiliated with, endorsed or supported by
  PRE (Pražská energetika) or SMATRICS. It uses undocumented interfaces that may change or stop working at any time.
- You use it **entirely at your own risk and responsibility**. The author accepts no liability
  for any damage or consequences of its use, in particular for **restriction or blocking of
  your account**, wrong or missing data (e.g. prices or charging status), or breaches of the
  service's terms of use.
- The same applies to **any modifications of the code or settings**, especially shortening
  the update interval below the defaults. That increases the load on the operator's servers
  and the risk of being blocked, and it is your own responsibility.
- Before using it, read the PRE Point / PRE CHARGE terms of service and decide for yourself whether
  using this integration complies with them.
- The software is provided "as is", without warranty of any kind (see the MIT license).

### Support

The integration is free and open source. If it saves you time or money, you can buy me a
coffee ☕

[![Buy me a coffee on Ko-fi](https://img.shields.io/badge/Ko--fi-buy%20me%20a%20coffee-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/parzival59520)

Bugs and ideas: [Issues](../../issues).

### License

MIT, see [LICENSE](LICENSE).
