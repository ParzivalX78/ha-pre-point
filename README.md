# PRE Point pro Home Assistant

[![Podpoř mě na Ko-fi](https://img.shields.io/badge/Ko--fi-kup%20mi%20kafe-FF5E5B?logo=kofi&logoColor=white)](https://ko-fi.com/parzival59520)

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
