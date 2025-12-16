"""
Klima Controller modul til intelligent automatisk vindues-styring.

Dette modul implementerer beslutnings-algoritmer til at optimere indeklimaet
ved automatisk styring af vindues åbning/lukning baseret på:
- Indendørs temperatur, fugtighed, og luftkvalitet (gas modstand)
- Udendørs temperatur og fugtighed (fra ESP32 (DHT11) eller måneds-gennemsnit)
- Konfigurerbare grænseværdier for komfort zoner
- Manuel override system (30 min efter user-kommando)
- Cooldown periods for at undgå irritation af støj og bevægelse

Beslutnings Logik:
    1. Tjek om manuel override er aktiv (skip automatik hvis ja)
    2. Tjek cooldown period siden sidste kommando (skip hvis stadig igang)
    3. Sammenlign indeklima med konfigurerede komfort grænser
    4. Sammenlign med udeklima for at vurdere forbedringspotentiale
    5. Beslut om vindue skal åbnes/lukkes/forblive uændret
    6. Vælg kommando type baseret på udendørs vejr kvalitet og indeklima

Komfort Zoner (fra config.py GRÆNSER):
    Temperatur:
        - Lav grænse: 19°C (under dette = for koldt)
        - Høj grænse: 22°C (over dette = for varmt)
        - Maksimum: 25°C (kritisk varmt, åbn med det samme)
    
    Fugtighed:
        - Lav grænse: 40% RH (under dette = for tørt)
        - Høj grænse: 60% RH (over dette = for fugtigt)
        - Maksimum: 70% RH (lummert og større mulighed for skimmelvækst)
    
    Luftkvalitet (Gas modstand):
        - Grænse linje: 45,000 Ohm (under = dårlig luft)
        - Minimum: 25,000 Ohm (Meget dårlig luft)
        - Optimal: >50,000 Ohm (god luftkvalitet)

Kommando Typer:
    'aaben':
        Fuld åbning af vindue for optimal udluftning.
        Bruges når:
        - Indeklima dårligt (varmt/fugtigt/dårlig luft)
        - Udeklima kan forbedre situation væsentligt
        - Udendørs vejr acceptabelt (temp >10°C, fugt <95%)
    
    'kort_aaben':
        5 minutters åbning derefter automatisk lukning.
        Bruges når:
        - Indeklima dårligt (udluftning nødvendig)
        - Udendørs vejr dårligt (koldt <10°C eller fugtigt >95%)
        - Minimerer energitab mens vi udlufter samt undgår for meget
          irriation for personer der sidder tæt på vinduet.
    
    'luk':
        Lukker vindue helt.
        Bruges når:
        - Indeklima er optimalt (alle målinger i komfort zone)
        - Indendørs for koldt (<19°C)
        - Udeklima ikke kan forbedre situation

Fallback Mekanisme:
    Hvis udendørs data ikke er tilgængeligt (ESP32 offline/dødt batteri),
    bruges månedlige gennemsnitsværdier for Danmark baseret på nuværende
    måned fra historiske meteorologiske data.
    
    Dette sikrer at systemet kan fortsætte med at fungere autonomt selv
    hvis ESP32 med sensor fejler permanent.

Manuel Override System:
    Når bruger sender 'manuel_aaben' eller 'manuel_luk' kommando via
    touchscreen, deaktiveres automatik i 30 minutter for at respektere
    brugerens valg.
    
    Override Logic:
        manuel_luk aktiverer:
            -> 30 min override -> Ingen automatik tilladt
            -> Respekterer at bruger bevidst lukkede vindue
        
        manuel_aaben aktiverer:
            -> 30 min override -> Ingen automatik tilladt
            -> Respekterer at bruger bevidst åbnede vindue
            -> Kan annullere tidligere manuel_luk override først
            -> Rationalet: Bruger vil have vinduet åbent
    
    Override udløber automatisk efter 30 minutter, hvorefter normal
    automatik genoptages.

Cooldown Periods:
    For at undgå hyppige vindues bevægelser (forstyrrende):
    
    Normal kommandoer (aaben/luk):
        - Cooldown: 30 minutter mellem kommandoer
        - Rationalet: Indeklima ændrer sig langsomt
    
    Kort åbning (kort_aaben):
        - Cooldown: 15 minutter mellem kommandoer
        - Rationalet: Bruges når der skal luftes ud, men ikke ønskes at forstyrre
          dem som sidder tæt på vinduet

Månedlige Gennemsnit (Danmark):
    Hvis ESP32 sensor er offline, bruges historiske vejrdata fra DMI og Vejrsiden:
    
    Temperatur (°C):
        Jan: 2, Feb: 2, Mar: 4, Apr: 7, Maj: 12, Jun: 15
        Jul: 17, Aug: 17, Sep: 14, Okt: 10, Nov: 6, Dec: 4
    
    Luftfugtighed (RH%):
        Jan: 86, Feb: 84, Mar: 81, Apr: 79, Maj: 77, Jun: 76
        Jul: 79, Aug: 80, Sep: 83, Okt: 85, Nov: 86, Dec: 87

Beslutnings Eksempler:
    Scenario 1 - Varmt og fugtigt indendørs:
        Indoor: 26°C, 70% RH, 30k Ohm gas
        Outdoor: 18°C, 55% RH
        Vindue: Lukket
        -> Beslutning: 'aaben'
        -> Grund: "Temp høj (26°C), Fugt høj (70%), Luft dårlig (30k Ohm)"
    
    Scenario 2 - Optimalt indeklima:
        Indoor: 21°C, 50% RH, 60k Ohm gas
        Outdoor: 18°C, 55% RH
        Vindue: Åben
        -> Beslutning: 'luk'
        -> Grund: "Indeklima optimalt"

Note:
    Da det er Python vi arbejder med, bruger vi type hints for klarhed.
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict
from config import GRÆNSER


# Månedlige gennemsnitstemperaturer for Danmark (°C)
# Data fra DMI 10-års normalperiode (2011-2020)

MÅNEDLIGE_GNSN_TEMP: Dict[int, int] = {
    1: 2,    # Januar
    2: 2,    # Februar
    3: 4,    # Marts
    4: 7,    # April
    5: 12,   # Maj
    6: 15,   # Juni
    7: 17,   # Juli
    8: 17,   # August
    9: 14,   # September
    10: 10,  # Oktober
    11: 6,   # November
    12: 4    # December
}
"""
Månedlige gennemsnittemperaturer baseret på Danmarks Meteorologiske Institut.

Data dækker 10-års normalperioden (2011-2020) og bruges som fallback
når ESP32 outdoor sensor er offline eller har dødt batteri.
"""

# Månedlige gennemsnitsfugtighed for Danmark (% RH)
# Data fra Vejrsiden.dk's månedelige gennemsnit

MÅNEDLIGE_GNSN_FUGT: Dict[int, int] = {
    1: 86,   # Januar
    2: 84,   # Februar
    3: 81,   # Marts
    4: 79,   # April
    5: 77,   # Maj
    6: 76,   # Juni
    7: 79,   # Juli
    8: 80,   # August
    9: 83,   # September
    10: 85,  # Oktober
    11: 86,  # November
    12: 87   # December
}
"""
Månedlige gennemsnitsfugtigheder baseret på Vejrsiden.dk.

Bruges som fallback når udendørs sensor data ikke er tilgængeligt.
"""


# Klima Controller klasse

class KlimaController:
    """
    Vores klimastyringssystem til automatisk vindueskontrol.
    
    Denne klasse implementerer beslutningslogik der evaluerer indeklima
    mod udeklima og sender kommandoer til ESP32 ved vinduet baseret på
    konfigurerbare komfort grænser og vejrforhold.
    
    Attributes:
        temp_lav/høj/maks: Temperatur grænser fra config (19/22/25°C)
        fugt_lav/høj/maks: Fugtigheds grænser fra config (40/60/70%)
        gas_lav/meget_lav: Gas modstands grænser (45k/25k Ohm)
        ude_temp_lav: Min outdoor temp for langvarig udluftning (10°C)
        ude_fugt_høj: Max outdoor fugt for langvarig udluftning (95%)
        sidste_kommando_tid: Timestamp for seneste kommando (datetime)
        sidste_kommando: Type af seneste kommando (str)
        kort_aaben_cooldown: Sekunder mellem kort_aaben (15 min)
        normal_cooldown: Sekunder mellem normale kommandoer (30 min)
        manuel_override_indtil: Timestamp for override udløb (datetime)
        manuel_override_varighed: Sekunder for override periode (30 min)
    """
    
    def __init__(self) -> None:
        """
        Initialiserer vores klima controller med konfigurerede grænseværdier.
        
        Indlæser alle komfort zone grænser fra config.py GRÆNSER dictionary
        og opsætter cooldown/override tracking state.
        """
        # Temperatur grænser fra vores config (Celsius)
        self.temp_lav: float = GRÆNSER['temp']['limit_low']
        self.temp_høj: float = GRÆNSER['temp']['limit_high']
        self.temp_maks: float = GRÆNSER['temp']['max']
        
        # Fugtigheds grænser fra vores config (% RH)
        self.fugt_lav: float = GRÆNSER['luftfugtighed']['limit_low']
        self.fugt_høj: float = GRÆNSER['luftfugtighed']['limit_high']
        self.fugt_maks: float = GRÆNSER['luftfugtighed']['max']
        
        # Gas (luftkvalitets) grænser fra vores config (Ohm)
        self.gas_lav: float = GRÆNSER['gas']['limit_line']
        self.gas_meget_lav: float = GRÆNSER['gas']['min']
        
        # Udendørs vejr grænser for langvarig udluftning
        self.ude_temp_lav: float = 10.0      # Under 10°C = kort udluftning
        self.ude_fugt_høj: float = 95.0      # Over 95% RH = kort udluftning
        
        # Cooldown system state
        self.sidste_kommando_tid: Optional[datetime] = None
        self.sidste_kommando: Optional[str] = None
        self.kort_aaben_cooldown: int = 15 * 60   # 15 minutter i sekunder
        self.normal_cooldown: int = 30 * 60       # 30 minutter i sekunder
        
        # Manuel override system state
        self.manuel_override_indtil: Optional[datetime] = None
        self.manuel_override_varighed: int = 30 * 60  # 30 minutter i sekunder
        
        # Debug
        print("Klima controller initialiseret")
        print(f"Temperatur grænser: {self.temp_lav}-{self.temp_høj}°C (max: {self.temp_maks}°C)")
        print(f"Fugtigheds grænser: {self.fugt_lav}-{self.fugt_høj}% (max: {self.fugt_maks}%)")
        print(f"Gas grænser: >{self.gas_lav}kOhm optimal, <{self.gas_meget_lav}kOhm kritisk")
    
    def _hent_nuværende_måneds_data(self) -> Tuple[int, int]:
        """
        Henter gennemsnits temperatur og fugtighed for nuværende måned.
        
        Bruges som fallback når ESP32 outdoor sensor er offline, dødt batteri,
        eller ikke sender data. Returnerer Danmarks vejrgennemsnit
        baseret på historiske DMI vejrdata samt data fra vejrdata.dk.
        
        Returns:
            Tuple[int, int]: (temperatur i °C, fugtighed i %)
        
        Eksempler:
            I: # Udført i januar
            I: temp, fugt = controller._hent_nuværende_måneds_data()
            I: temp, fugt
            O: (2, 86)
            
            I: # Udført i juli
            I: temp, fugt = controller._hent_nuværende_måneds_data()
            I: temp, fugt
            O: (17, 79)
        
        Note:
            Hvis måneds-lookup fejler (burde aldrig ske), returneres
            default værdier: 10°C og 80% RH.
        """
        # Hent nuværende måned (1-12)
        nuværende_måned = datetime.now().month
        
        # Lookup i vores dictionaries med fallback
        gennemsnits_temp = MÅNEDLIGE_GNSN_TEMP.get(nuværende_måned, 10)
        gennemsnits_fugt = MÅNEDLIGE_GNSN_FUGT.get(nuværende_måned, 80)
        
        return gennemsnits_temp, gennemsnits_fugt
    
    def _kan_sende_kommando(self) -> bool:
        """
        Tjekker om ny kommando kan sendes baseret på cooldown og override.
        
        Denne metode håndhæver to restriktioner der forhindrer overdreven
        vindues aktivitet:
        1. Manuel override: Hvis aktiv, ingen automatiske kommandoer tilladt
        2. Cooldown period: Minimum tid mellem kommandoer
        
        Returns:
            True: Kommando kan sendes (ingen restriktioner aktive)
            False: Kommando blokeret (override eller cooldown aktiv)
        
        Eksempler:
            I: # Lige efter 'aaben' kommando sendt
            I: controller.gem_kommando('aaben')
            I: controller._kan_sende_kommando()
            O: False  # Indenfor 30 min cooldown
            
            I: # 31 minutter senere
            I: controller._kan_sende_kommando()
            O: True  # Cooldown udløbet
        
        Note:
            Metoden nulstiller automatisk udløbet manuel override,
            så vi slipper for at have en separat cleanup metode.
        """
        # Tjek manuel override først (højeste prioritet)
        if self.manuel_override_indtil:
            if datetime.now() < self.manuel_override_indtil:
                # Override stadig aktiv, bloker automatik
                return False
            else:
                # Override udløbet, nulstil og fortsæt
                print("Manuel override udløbet - genoptager automatik")
                self.manuel_override_indtil = None
        
        # Hvis ingen tidligere kommando, tillad (første kommando)
        if self.sidste_kommando_tid is None:
            return True
        
        # Beregn tid siden sidste kommando (sekunder)
        tid_gået = (datetime.now() - self.sidste_kommando_tid).total_seconds()
        
        # Tjek cooldown baseret på kommando type
        if self.sidste_kommando == 'kort_aaben':
            # Kort åbning har kortere cooldown (15 min)
            cooldown_udløbet = tid_gået >= self.kort_aaben_cooldown
            
            if not cooldown_udløbet:
                # Debug
                mangler = int(self.kort_aaben_cooldown - tid_gået)
                print(f"Cooldown aktiv: {mangler}sekunder tilbage")
            
            return cooldown_udløbet
        
        # Normal kommando har standard cooldown (30 min)
        cooldown_udløbet = tid_gået >= self.normal_cooldown
        
        if not cooldown_udløbet:
            # Debug
            mangler = int(self.normal_cooldown - tid_gået)
            print(f"Cooldown aktiv: {mangler}sekunder tilbage")
        
        return cooldown_udløbet
    
    def _ude_vejr_dårligt(
        self,
        ude_temp: float,
        ude_fugt: float
    ) -> bool:
        """
        Tjekker om udendørs vejr er for dårligt til langvarig udluftning.
        
        "Dårligt vejr" defineres som vejrforhold der gør langvarig vindues
        åbning uønsket pga. at det kan virke forstyrrende for omkringsiddende:
        - Temperatur under 10°C (for koldt)
        - Fugtighed over 95% RH (bringer fugtig luft ind - muligvis regn)
        
        Args:
            ude_temp: Udendørs temperatur i °C
            ude_fugt: Udendørs relativ luftfugtighed i %
        
        Returns:
            True: Vejret er for dårligt (brug kort_aaben i stedet)
            False: Vejret er acceptabelt (normal aaben OK)
        
        Eksempler:
            I: controller._ude_vejr_dårligt(8, 70)
            O: True  # For koldt (<10°C)
            
            I: controller._ude_vejr_dårligt(15, 90)
            O: True  # For fugtigt (>85%)
            
            I: controller._ude_vejr_dårligt(15, 60)
            O: False  # Acceptabelt vejr
        
        Note:
            Vi bruger OR logik - hvis ENTEN temp eller fugt er dårligt,
            returneres True. De behøver ikke begge at være opfyldt.
        """
        # Tjek om det er for koldt
        for_koldt = ude_temp < self.ude_temp_lav
        
        # Tjek om det er for fugtigt
        for_fugtigt = ude_fugt > self.ude_fugt_høj
        
        # Hvis en af dem er true, er vejret dårligt
        return for_koldt or for_fugtigt
    
    def aktiver_manuel_override(self, kommando: str) -> None:
        """
        Aktiverer 30 minutters manuel override af automatik.
        
        Når bruger sender 'manuel_aaben' eller 'manuel_luk' kommando fra
        touchscreen, deaktiveres automatikken i 30 minutter for at
        respektere brugerens valg.
        
        Begge kommandoer aktiverer override da brugeren har taget en
        bevidst beslutning som systemet skal respektere.
        
        Args:
            kommando: Manuel kommando der udløste override
                     ('manuel_aaben' eller 'manuel_luk')
        
        Eksempler:
            I: controller.aktiver_manuel_override('manuel_luk')
            I: # Nu er automatik deaktiveret i 30 min
            I: controller._kan_sende_kommando()
            O: False
            
            I: controller.aktiver_manuel_override('manuel_aaben')
            I: # Nu er automatik deaktiveret i 30 min
            I: controller._kan_sende_kommando()
            O: False
        
        Note:
            Hvis bruger sender ny manuel kommando mens override aktiv,
            resettes perioden til fuld 30 min timer på ny.
        """
        # Sæt override til at udløbe om 30 minutter
        self.manuel_override_indtil = (
            datetime.now() + timedelta(seconds=self.manuel_override_varighed)
        )
        
        # Debug
        print(f"Manuel override aktiveret i 30 min pga. {kommando}")
        
        # Log til database for audit trail
        from database import db
        from config import ENHEDS_ID
        db.gem_system_log(
            ENHEDS_ID,
            'ClimateCtrl',
            f'Manuel override aktiveret i 30 min pga. {kommando}'
        )
    
    def annuller_manuel_override_hvis_manuel_åben(
        self,
        kommando: str
    ) -> None:
        """
        Annullerer aktiv manuel override hvis kommando er manuel_aaben.
        
        Denne metode tillader manuel_aaben at annullere en eksisterende
        manuel_luk override INDEN den sætter sin egen 30 min override.
        
        Dette implementerer en "refresh" logik hvor:
        - Bruger lukker manuelt -> 30 min override starter
        - 5 min senere: Bruger åbner manuelt -> Gammel override annulleres
        - Ny 30 min override starter fra manuel_aaben
        
        Args:
            kommando: Kommando at tjekke ('manuel_aaben' kan annulleres)
        
        Eksempler:
            I: # Bruger lukkede manuelt
            I: controller.aktiver_manuel_override('manuel_luk')
            I: # 5 minutter senere: Bruger åbner manuelt
            I: controller.annuller_manuel_override_hvis_manuel_åben('manuel_aaben')
            I: controller.manuel_override_indtil
            O: None  # Gammel override annulleret
            I: # Nu sættes ny override fra manuel_aaben
            I: controller.aktiver_manuel_override('manuel_aaben')
        
        Note:
            Kun manuel_aaben kan annullere eksisterende override før
            den sætter sin egen. manuel_luk refresher bare sin timer.
        """
        # Tjek om det er manuel åben OG override er aktiv
        if kommando == 'manuel_aaben' and self.manuel_override_indtil:
            # Nulstil override så ny kan sættes
            self.manuel_override_indtil = None
            
            # Debug
            print("Eksisterende manuel override aflyst pga. manuel_aaben")
            
            # Log annullering til database
            from database import db
            from config import ENHEDS_ID
            db.gem_system_log(
                ENHEDS_ID,
                'ClimateCtrl',
                'Eksisterende manuel override aflyst - ny override startes'
            )
    
    def vurder_klima(
        self,
        inden_temp: float,
        inden_fugt: float,
        inden_gas: Optional[float],
        ude_temp: Optional[float],
        ude_fugt: Optional[float],
        vindue_status: str
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Hovedfunktion der vurderer inde- og udeklimaet og beslutter vindues kommando.
        
        Dette er den centrale beslutnings algoritme der integrerer alle
        aspekter af klimaevalueringen: indoor målinger, outdoor vejr,
        vindues status, override system, og cooldown tracking.
        
        Args:
            inden_temp: Indendørs temperatur i °C (fra BME680)
            inden_fugt: Indendørs fugtighed i % RH (fra BME680)
            inden_gas: Gas modstand i kOhm (fra BME680, None hvis unstable)
            ude_temp: Udendørs temperatur i °C (fra ESP32 (DHT11), None = fallback)
            ude_fugt: Udendørs fugtighed i % RH (fra ESP32 (DHT11), None = fallback)
            vindue_status: Nuværende status ('aaben', 'lukket', 'ukendt')
        
        Returns:
            Tuple[Optional[str], Optional[str]]:
            - kommando: 'aaben', 'kort_aaben', 'luk', eller None
            - grund: (data-string) eller None
        
        Eksempler:
            I: # Indendørs for varmt og fugtigt
            I: kommando, grund = controller.vurder_klima(
                inden_temp=26, inden_fugt=70, inden_gas=50000,
                ude_temp=18, ude_fugt=55, vindue_status='lukket'
                )
            I: kommando, grund
            O: ('aaben', 'Åbner vindue: Temp høj (26°C), Fugt høj (70%)')
            
            I: # Indeklima optimalt, vindue åbent
            I: kommando, grund = controller.vurder_klima(
                inden_temp=21, inden_fugt=50, inden_gas=60000,
                ude_temp=18, ude_fugt=55, vindue_status='aaben'
                )
            I: kommando, grund
            O: ('luk', 'Indeklima optimalt')
        
        Note:
            Hvis ESP32 aldrig kommer online (permanent død), fortsætter
            systemet med måneds-gennemsnit. Beslutningerne er mindre optimale men
            stadig funktionelle - bedre end at stoppe automatik helt.
        """
        # Valider essentielle indoor data
        if None in [inden_temp, inden_fugt]:
            print("Mangler indendørs data - springer vurdering over")
            return None, None
        
        # Tjek manuel override (højeste prioritet)
        if self.manuel_override_indtil and datetime.now() < self.manuel_override_indtil:
            # Debug - hvor lang tid tilbage
            tid_tilbage = (self.manuel_override_indtil - datetime.now()).total_seconds()
            print(f"Manuel override aktiv - {int(tid_tilbage/60)} min tilbage")
            return None, None
        
        # Fallback til måneds-gennemsnit hvis outdoor data mangler
        if ude_temp is None or ude_fugt is None:
            gnsn_temp, gnsn_fugt = self._hent_nuværende_måneds_data()
            ude_temp = gnsn_temp if ude_temp is None else ude_temp
            ude_fugt = gnsn_fugt if ude_fugt is None else ude_fugt
            
            # Debug
            print(f"Bruger måneds-gennemsnit: {ude_temp}°C, {ude_fugt}%")
            
            # Log fallback usage
            from database import db
            from config import ENHEDS_ID
            db.gem_system_log(
                ENHEDS_ID,
                'ClimateCtrl',
                f'Bruger måneds-gennemsnit: {ude_temp}°C, {ude_fugt}%'
            )
        
        # Tjek cooldown period
        if not self._kan_sende_kommando():
            return None, None
        
        # Evaluer indeklima kvalitet
        for_varmt = inden_temp > self.temp_høj
        for_fugtigt = inden_fugt > self.fugt_høj
        
        # Gas sensor checks (None-safe - reagerer ikke på none i beregning)
        meget_dårlig_luft = inden_gas is not None and inden_gas < self.gas_meget_lav
        dårlig_luft = inden_gas is not None and inden_gas < self.gas_lav
        
        # Samlet vurdering
        dårlig_luftkvalitet = for_varmt or for_fugtigt or dårlig_luft
        
        # Debug - indeklima status
        if dårlig_luftkvalitet:
            print(f"Dårligt indeklima: Temp={inden_temp}°C, Fugt={inden_fugt}%, Gas={inden_gas}Ω")
        
        # Vindue åben logik
        if vindue_status == 'aaben':
            # Tjek om indeklima er blevet optimalt
            temp_ok = self.temp_lav <= inden_temp <= self.temp_høj
            fugt_ok = self.fugt_lav <= inden_fugt <= self.fugt_høj
            gas_ok = inden_gas is None or inden_gas > self.gas_lav
            
            # Debug
            print(f"Vindue åbent - Temp OK: {temp_ok}, Fugt OK: {fugt_ok}, Gas OK: {gas_ok}")
            
            # Alle parametre optimale -> luk
            if temp_ok and fugt_ok and gas_ok:
                print("Alle parametre optimale - lukker vindue")
                return 'luk', "Indeklima optimalt"
            
            # For koldt -> luk)
            if inden_temp < self.temp_lav:
                print(f"For koldt ({inden_temp}°C) - lukker vindue")
                return 'luk', f"Lukker pga kulde - {inden_temp}°C"
            
            # Ellers fortsæt udluftning
            print("Fortsætter udluftning")
            return None, None
        
        # Vindue lukket logik
        # Hvis indeklima er fint, gør intet
        if not dårlig_luftkvalitet:
            print("Indeklima fint - ingen handling udføres")
            return None, None
        
        # Tjek om udeklima kan forbedre situationen
        ude_temp_bedre = for_varmt and (ude_temp < inden_temp)
        ude_fugt_bedre = for_fugtigt and (ude_fugt < inden_fugt)
        ude_gas_bedre = dårlig_luft  # luften er altid bedre udenfor
        
        # Hvis outdoor ikke kan hjælpe, vent
        if not (ude_temp_bedre or ude_fugt_bedre or ude_gas_bedre):
            print("Udeklima kan ikke forbedre situationen")
            return None, None
        
        # Meget dårlig luft = åben med det samme
        if meget_dårlig_luft:
            print(f"Meget dårlig luft ({int(inden_gas)}kΩ) - åbner vindue")
            return (
                'aaben',
                f"Meget dårligt indeklima: {int(inden_gas)}kΩ - Skal lufte ud"
            )
        
        # Byg detaljeret grund string
        grunde = []
        if for_varmt and ude_temp_bedre:
            grunde.append(f"Temp høj ({inden_temp}°C)")
        if for_fugtigt and ude_fugt_bedre:
            grunde.append(f"Fugt høj ({inden_fugt}%)")
        if dårlig_luft:
            grunde.append(f"Luft dårlig ({int(inden_gas)}kΩ)")
        
        grund_tekst = ", ".join(grunde)
        
        # Debug
        print(f"Åbner vindue: {grund_tekst}")
        
        # Beslut kommando baseret på udendørs vejr
        if self._ude_vejr_dårligt(ude_temp, ude_fugt):
            print(f"Udendørs vejr dårligt ({ude_temp}°C, {ude_fugt}%) - kort åbning")
            return (
                'kort_aaben',
                f"Kort udluftning - vejret er dårligt udenfor: {grund_tekst}"
            )
        else:
            print(f"Udendørs vejr acceptabelt ({ude_temp}°C, {ude_fugt}%) - normal åbning")
            return 'aaben', f"Åbner vindue: {grund_tekst}"
    
    def gem_kommando(self, kommando: str) -> None:
        """
        Gemmer information om den sendte kommando for cooldown tracking.
        
        Opdaterer sidste_kommando og sidste_kommando_tid så cooldown
        systemet kan håndhæve minimum tid mellem kommandoer.
        
        Args:
            kommando: Kommando der lige blev sendt
                     ('aaben', 'kort_aaben', 'luk', 'manuel_aaben', 'manuel_luk')
        
        Eksempler:
            I: controller.gem_kommando('aaben')
            I: controller.sidste_kommando
            O: 'aaben'
            I: controller._kan_sende_kommando()
            O: False  # Cooldown aktiv
        
        Note:
            Kaldes fra indoor_sensor.py efter successful MQTT publish.
        """
        self.sidste_kommando = kommando
        self.sidste_kommando_tid = datetime.now()
        
        # Debug
        print(f"Kommando gemt: {kommando} på {self.sidste_kommando_tid.strftime('%H:%M:%S')}")


# Global singleton instance

klima_controller: KlimaController = KlimaController()
"""
Global klima controller instance bruges af hele systemet.

Denne singleton bruges til klima evaluering og vindues beslutninger.
Alle komponenter importerer samme instance for at sikre konsistent
state tracking på tværs af cooldown og override periods.

Import:
    from climate_controller import klima_controller
    
    kommando, grund = klima_controller.vurder_klima(...)
    klima_controller.gem_kommando('aaben')
    klima_controller.aktiver_manuel_override('manuel_aaben')
    klima_controller.annuller_manuel_override_hvis_manuel_åben('manuel_aaben')

Note:
    Singleton Pattern sikrer at cooldown tracking fungerer korrekt
    på tværs af alle kald, og at manuel override state bevares.
"""