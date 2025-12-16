

from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict
from config import GRÆNSER



MÅNEDLIGE_GNSN_TEMP: Dict[int, int] = {
    1: 2,
    2: 2,
    3: 4,
    4: 7,
    5: 12,
    6: 15,
    7: 17,
    8: 17,
    9: 14,
    10: 10,
    11: 6,
    12: 4
}



MÅNEDLIGE_GNSN_FUGT: Dict[int, int] = {
    1: 86,
    2: 84,
    3: 81,
    4: 79,
    5: 77,
    6: 76,
    7: 79,
    8: 80,
    9: 83,
    10: 85,
    11: 86,
    12: 87
}




class KlimaController:

    
    def __init__(self) -> None:

        self.temp_lav: float = GRÆNSER['temp']['limit_low']
        self.temp_høj: float = GRÆNSER['temp']['limit_high']
        self.temp_maks: float = GRÆNSER['temp']['max']
        
        self.fugt_lav: float = GRÆNSER['luftfugtighed']['limit_low']
        self.fugt_høj: float = GRÆNSER['luftfugtighed']['limit_high']
        self.fugt_maks: float = GRÆNSER['luftfugtighed']['max']
        
        self.gas_lav: float = GRÆNSER['gas']['limit_line']
        self.gas_meget_lav: float = GRÆNSER['gas']['min']
        
        self.ude_temp_lav: float = 10.0
        self.ude_fugt_høj: float = 95.0
        
        self.sidste_kommando_tid: Optional[datetime] = None
        self.sidste_kommando: Optional[str] = None
        self.kort_aaben_cooldown: int = 15 * 60
        self.normal_cooldown: int = 30 * 60
        
        self.manuel_override_indtil: Optional[datetime] = None
        self.manuel_override_varighed: int = 30 * 60
        
        print("Klima controller initialiseret")
        print(f"Temperatur grænser: {self.temp_lav}-{self.temp_høj}°C (max: {self.temp_maks}°C)")
        print(f"Fugtigheds grænser: {self.fugt_lav}-{self.fugt_høj}% (max: {self.fugt_maks}%)")
        print(f"Gas grænser: >{self.gas_lav}kOhm optimal, <{self.gas_meget_lav}kOhm kritisk")
    
    def _hent_nuværende_måneds_data(self) -> Tuple[int, int]:

        nuværende_måned = datetime.now().month
        
        gennemsnits_temp = MÅNEDLIGE_GNSN_TEMP.get(nuværende_måned, 10)
        gennemsnits_fugt = MÅNEDLIGE_GNSN_FUGT.get(nuværende_måned, 80)
        
        return gennemsnits_temp, gennemsnits_fugt
    
    def _kan_sende_kommando(self) -> bool:

        if self.manuel_override_indtil:
            if datetime.now() < self.manuel_override_indtil:
                return False
            else:
                print("Manuel override udløbet - genoptager automatik")
                self.manuel_override_indtil = None
        
        if self.sidste_kommando_tid is None:
            return True
        
        tid_gået = (datetime.now() - self.sidste_kommando_tid).total_seconds()
        
        if self.sidste_kommando == 'kort_aaben':
            cooldown_udløbet = tid_gået >= self.kort_aaben_cooldown
            
            if not cooldown_udløbet:
                mangler = int(self.kort_aaben_cooldown - tid_gået)
                print(f"Cooldown aktiv: {mangler}sekunder tilbage")
            
            return cooldown_udløbet
        
        cooldown_udløbet = tid_gået >= self.normal_cooldown
        
        if not cooldown_udløbet:
            mangler = int(self.normal_cooldown - tid_gået)
            print(f"Cooldown aktiv: {mangler}sekunder tilbage")
        
        return cooldown_udløbet
    
    def _ude_vejr_dårligt(
        self,
        ude_temp: float,
        ude_fugt: float
    ) -> bool:

        for_koldt = ude_temp < self.ude_temp_lav
        
        for_fugtigt = ude_fugt > self.ude_fugt_høj
        
        return for_koldt or for_fugtigt
    
    def aktiver_manuel_override(self, kommando: str) -> None:

        self.manuel_override_indtil = (
            datetime.now() + timedelta(seconds=self.manuel_override_varighed)
        )
        
        print(f"Manuel override aktiveret i 30 min pga. {kommando}")
        
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

        if kommando == 'manuel_aaben' and self.manuel_override_indtil:
            self.manuel_override_indtil = None
            
            print("Eksisterende manuel override aflyst pga. manuel_aaben")
            
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

        if None in [inden_temp, inden_fugt]:
            print("Mangler indendørs data - springer vurdering over")
            return None, None
        
        if self.manuel_override_indtil and datetime.now() < self.manuel_override_indtil:
            tid_tilbage = (self.manuel_override_indtil - datetime.now()).total_seconds()
            print(f"Manuel override aktiv - {int(tid_tilbage/60)} min tilbage")
            return None, None
        
        if ude_temp is None or ude_fugt is None:
            gnsn_temp, gnsn_fugt = self._hent_nuværende_måneds_data()
            ude_temp = gnsn_temp if ude_temp is None else ude_temp
            ude_fugt = gnsn_fugt if ude_fugt is None else ude_fugt
            
            print(f"Bruger måneds-gennemsnit: {ude_temp}°C, {ude_fugt}%")
            
            from database import db
            from config import ENHEDS_ID
            db.gem_system_log(
                ENHEDS_ID,
                'ClimateCtrl',
                f'Bruger måneds-gennemsnit: {ude_temp}°C, {ude_fugt}%'
            )
        
        if not self._kan_sende_kommando():
            return None, None
        
        for_varmt = inden_temp > self.temp_høj
        for_fugtigt = inden_fugt > self.fugt_høj
        
        meget_dårlig_luft = inden_gas is not None and inden_gas < self.gas_meget_lav
        dårlig_luft = inden_gas is not None and inden_gas < self.gas_lav
        
        dårlig_luftkvalitet = for_varmt or for_fugtigt or dårlig_luft
        
        if dårlig_luftkvalitet:
            print(f"Dårligt indeklima: Temp={inden_temp}°C, Fugt={inden_fugt}%, Gas={inden_gas}Ω")
        
        if vindue_status == 'aaben':
            temp_ok = self.temp_lav <= inden_temp <= self.temp_høj
            fugt_ok = self.fugt_lav <= inden_fugt <= self.fugt_høj
            gas_ok = inden_gas is None or inden_gas > self.gas_lav
            
            print(f"Vindue åbent - Temp OK: {temp_ok}, Fugt OK: {fugt_ok}, Gas OK: {gas_ok}")
            
            if temp_ok and fugt_ok and gas_ok:
                print("Alle parametre optimale - lukker vindue")
                return 'luk', "Indeklima optimalt"
            
            if inden_temp < self.temp_lav:
                print(f"For koldt ({inden_temp}°C) - lukker vindue")
                return 'luk', f"Lukker pga kulde - {inden_temp}°C"
            
            print("Fortsætter udluftning")
            return None, None
        
        if not dårlig_luftkvalitet:
            print("Indeklima fint - ingen handling udføres")
            return None, None
        
        ude_temp_bedre = for_varmt and (ude_temp < inden_temp)
        ude_fugt_bedre = for_fugtigt and (ude_fugt < inden_fugt)
        ude_gas_bedre = dårlig_luft
        
        if not (ude_temp_bedre or ude_fugt_bedre or ude_gas_bedre):
            print("Udeklima kan ikke forbedre situationen")
            return None, None
        
        if meget_dårlig_luft:
            print(f"Meget dårlig luft ({int(inden_gas)}kΩ) - åbner vindue")
            return (
                'aaben',
                f"Meget dårligt indeklima: {int(inden_gas)}kΩ - Skal lufte ud"
            )
        
        grunde = []
        if for_varmt and ude_temp_bedre:
            grunde.append(f"Temp høj ({inden_temp}°C)")
        if for_fugtigt and ude_fugt_bedre:
            grunde.append(f"Fugt høj ({inden_fugt}%)")
        if dårlig_luft:
            grunde.append(f"Luft dårlig ({int(inden_gas)}kΩ)")
        
        grund_tekst = ", ".join(grunde)
        
        print(f"Åbner vindue: {grund_tekst}")
        
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

        self.sidste_kommando = kommando
        self.sidste_kommando_tid = datetime.now()
        
        print(f"Kommando gemt: {kommando} på {self.sidste_kommando_tid.strftime('%H:%M:%S')}")



klima_controller: KlimaController = KlimaController()
