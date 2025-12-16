"""
Matplotlib Graf Generator til visualisering af sensor data.

Dette modul implementerer server-side graf rendering der:
- Genererer matplotlib grafer som PNG billeder
- Visualiserer historisk sensor data (temp, fugtighed, gas)
- Sammenligner indendørs vs udendørs målinger
- Håndterer missing data gracefully med placeholder grafer
- Bruger non-GUI backend for headless server operation

Graf Typer:
    Temperatur: Indendørs (BME680) vs Udendørs (DHT11)
    Luftfugtighed: Indendørs (BME680) vs Udendørs (DHT11)
    Gas: Kun indendørs (BME680 luftkvalitet)

Styling:
    Dark theme: Sort baggrund med neon farver
    Color-coded: Unikke farver per data type
    Grid lines: For bedre læsbarhed
    Legend: Auto-positioned baseret på data
    Date formatting: Dansk locale hvis tilgængeligt

Performance:
    Non-GUI backend: Ingen X11 dependency (Agg backend)
    BytesIO streaming: Ingen disk - intern hukommelse
    On-demand generation: Grafer genereres ved request
    Memory cleanup: Explicit plt.close() efter hver graf

Brug:
    from graph_generator import graf_generator
    billede = graf_generator.generer_graf('temperatur', dage=7)
    # billede er BytesIO objekt klar til HTTP streaming
"""

import matplotlib
matplotlib.use('Agg')  # Non-GUI backend for headless server (funktionalitet)
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.axes import Axes
from datetime import datetime, timedelta #Timedelta bruges til vores 7-dages logik
from io import BytesIO
from typing import Dict, List, Optional, Any, Tuple
from database import db
from config import ENHEDS_ID
import locale

# Locale setup for danske datoer
try:
    locale.setlocale(locale.LC_TIME, 'da_DK.UTF-8')
except locale.Error as fejl:
    # Fallback til default locale hvis dansk ikke tilgængeligt
    db.gem_fejl(
        ENHEDS_ID,
        'GRAPH_GENERATOR',
        f"Kunne ikke sætte dansk locale: {fejl}. Bruger system default."
    )


class GrafGenerator:
    """
    Generator til matplotlib sensor data grafer.
    
    Denne klasse håndterer al graf generering for systemet. Den implementerer
    three-in-one funktionalitet: temperatur, fugtighed og gas visualisering
    med konsistent styling og automatisk data handling.
    
    Matplotlib Backend:
        Bruger 'Agg' (Anti-Grain Geometry) backend som er non-interactive
        og kræver ingen GUI libraries.
        
        Backend valg:
        - Agg: Rasterization til PNG (vores valg)
        - SVG: Vector graphics (ikke brugt, større filer)
        - PDF: Print-ready (ikke brugt, indeholder en masse metadata)
    
    Graf Konfiguration:
        Hver graf type har sin egen config med:
        - title: Graf overskrift
        - unit: Måleenhed (°C, %, kΩ)
        - indoor_color: Neon farve til indendørs data
        - outdoor_color: Neon farve til udendørs (None for gas)
        - labels: Legend tekst
        
        Farver valgt for:
        - God kontrast mod sort baggrund
        - Farveblinde-venlig (forskellige hues)
        - Moderne neon aesthetic
    
    Attributter:
        configs: Dictionary med graf konfigurationer per data type
    
    Eksempler:
        generator = GrafGenerator()
        temp_graf = generator.generer_graf('temperatur', dage=7)
        # Returnerer BytesIO med PNG
    """
    
    def __init__(self) -> None:
        """
        Initialiserer graf generator med styling konfiguration.
        
        Opsætter:
        - Dark background theme (matplotlib style)
        - Graf konfigurationer for alle tre data typer
        - Color palette til konsistent styling
        
        Dark Background Theme:
            plt.style.use('dark_background') sætter globale defaults:
            - Figure facecolor: #000000 (sort)
            - Axes facecolor: #111111 (næsten sort)
            - Text color: #FFFFFF (hvid)
            - Grid color: #CCCCCC (lysegrå)
        
        Note:
            Matplotlib style 'dark_background' sættes globalt og påvirker
            alle efterfølgende plt.subplots() kald i denne process.
        """
        # Matplotlib styling
        plt.style.use('dark_background')
        
        # Graf konfigurationer
        self.configs: Dict[str, Dict[str, Optional[str]]] = {
            'temperatur': {
                'title': 'Temperatur - Sidste 7 Dage',
                'unit': '°C',
                'indoor_color': "#FFDD00",
                'outdoor_color': "#00D9CB",
                'indoor_label': 'Indendørs',
                'outdoor_label': 'Udendørs'
            },
            'luftfugtighed': {
                'title': 'Luftfugtighed - Sidste 7 Dage',
                'unit': '%',
                'indoor_color': "#FF8400",
                'outdoor_color': "#00FFFF",
                'indoor_label': 'Indendørs',
                'outdoor_label': 'Udendørs'
            },
            'gas': {
                'title': 'Luftkvalitet (Gas) - Sidste 7 Dage',
                'unit': 'kΩ',
                'indoor_color': "#FFB300",
                'outdoor_color': None,          # Ingen udendørs gas måling
                'indoor_label': 'Gas Modstand',
                'outdoor_label': None
            }
        }
    
    def _valider_data_type(self, data_type: str) -> None:
        """
        Validerer at data_type er supported.
        
        Args:
            data_type: Type at validere
        
        Raises:
            ValueError: Hvis data_type ikke er i configs
        
        Note:
            Helper funktion til input validation i alle public metoder.
        """
        if data_type not in self.configs:
            gyldige_typer = ', '.join(self.configs.keys())
            raise ValueError(
                f"Ugyldig data type: '{data_type}'. "
                f"Gyldige typer: {gyldige_typer}"
            )
    
    def _valider_dage(self, dage: int) -> None:
        """
        Validerer at dage-parameter er indenfor acceptabelt range.
        
        Args:
            dage: Antal dage at validere
        
        Raises:
            ValueError: Hvis dage er udenfor range 1-14
            TypeError: Hvis dage ikke er int
        
        Range Rationale:
            1 dag minimum: Mindre giver ikke meningsfuld visualisering
            14 dage maximum: Mere giver overfyldte grafer der er svære at læse
            på den lille skærm
        
        Note:
            Range limitation forhindrer "excessive database queries"
            og for store grafer der er svære at læse på skærmen.
        """
        if not isinstance(dage, int):
            raise TypeError(f"dage skal være int, ikke {type(dage).__name__}")
        
        if dage < 1 or dage > 14:
            raise ValueError(
                f"dage skal være mellem 1 og 14, fik {dage}"
            )
    
    def _organiser_data(
            self,
            data: List[Dict[str, Any]]
        ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            """
            Opdeler data i indoor og outdoor baseret på kilde.
            
            Args:
                data: Liste af sensor målinger med 'kilde' felt
            
            Returns:
                Tuple med (indoor_data, outdoor_data) lister
            
            Kilde Matching:
                Indoor: 'BME680' i kilde string (case-insensitive)
                Outdoor: 'ESP32', 'DHT', 'OUTDOOR', 'UDE' i kilde string
            
            Eksempler:
                data = [
                    {'kilde': 'BME680', 'værdi': 22.5},
                    {'kilde': 'ESP32_UDENFOR', 'værdi': 18.2},
                    {'kilde': 'DHT11', 'værdi': 17.8}
                ]
                indoor, outdoor = generator._organiser_data(data)
                len(indoor), len(outdoor)
                (1, 2)
            
            Note:
                Bruger case-insensitive string matching for robusthed.
                Matcher flere mulige outdoor sensor navne for flexibility.
            """
            indoor_data: List[Dict[str, Any]] = []
            outdoor_data: List[Dict[str, Any]] = []
            
            # Outdoor sensor identifiers (case-insensitive)
            outdoor_identifiers = ['ESP32', 'DHT', 'OUTDOOR', 'UDE', 'UDENFOR']
            
            for d in data:
                kilde_upper = d['kilde'].upper()
                
                # Tjek om det er indoor sensor
                if 'BME680' in kilde_upper:
                    indoor_data.append(d)
                # Tjek om det er outdoor sensor
                elif any(identifier in kilde_upper for identifier in outdoor_identifiers):
                    outdoor_data.append(d)
                else:
                    # Log ukendt kilde for debugging
                    print(f"Ukendt sensor kilde: {d['kilde']}")
            
            return indoor_data, outdoor_data
    
    def _konverter_gas_til_kiloohm(
        self,
        data: List[Dict[str, Any]]
    ) -> List[float]:
        """
        Konverterer gas modstand fra Ohm til kiloOhm for læsbarhed.
        
        Args:
            data: Liste af gas målinger med 'værdi' i Ohm
        
        Returns:
            Liste af værdier i kΩ (kiloOhm)
        
        Conversion:
            Gas værdier typisk 25k-50k Ohm, så kΩ er mere læsbar:
            - 45000 Ohm -> 45.0 kΩ (mindre tal, lettere at læse)
            - Y-axis labels kortere og renere
        
        Eksempler:
            data = [{'værdi': 45000}, {'værdi': 50000}]
            generator._konverter_gas_til_kiloohm(data)
            [45.0, 50.0]
        
        Note:
            Division med 1000.0 (float) sikrer float output
        """
        return [d['værdi'] / 1000.0 for d in data]
    
    def _udvind_timestamps(
        self,
        data: List[Dict[str, Any]]
    ) -> List[datetime]:
        """
        Konverterer ISO timestamp strings til datetime objekter.
        
        Args:
            data: Liste af målinger med 'målt_klokken' felt
        
        Returns:
            Liste af datetime objekter
        
        Raises:
            ValueError: Hvis timestamp-format er ugyldigt
        
        ISO 8601 Format:
            YYYY-MM-DDTHH:MM:SS.mmmmmm
            Eksempel: 2025-12-12T10:30:00.123456
        
        Eksempler:
            data = [{'målt_klokken': '2025-12-12T10:30:00'}]
            timestamps = generator._ekstraher_timestamps(data)
            isinstance(timestamps[0], datetime)
            True
        """
        try:
            return [
                datetime.fromisoformat(d['målt_klokken']) 
                for d in data
            ]
        except (ValueError, KeyError) as fejl:
            raise ValueError(f"Ugyldig timestamp format: {fejl}")
    
    def _udvind_værdier(
        self,
        data: List[Dict[str, Any]],
        data_type: str
    ) -> List[float]:
        """
        Aflæser værdier fra data med type-specifik konvertering.
        
        Args:
            data: Liste af målinger med 'værdi' felt
            data_type: Type af data for at bestemme konvertering
        
        Returns:
            Liste af værdier (gas konverteret til kΩ)
        
        Type-specifik prossesering:
            temperatur: Direkte værdi (°C)
            luftfugtighed: Direkte værdi (%)
            gas: konverteres til kΩ (divider med 1000)
        
        Eksempler:
            data = [{'værdi': 22.5}]
            generator._ekstraher_værdier(data, 'temperatur')
            [22.5]
            
            gas_data = [{'værdi': 45000}]
            generator._ekstraher_værdier(gas_data, 'gas')
            [45.0]
        """
        if data_type == 'gas':
            return self._konverter_gas_til_kiloohm(data)
        else:
            return [d['værdi'] for d in data]
    
    def _plot_data_serie(
        self,
        ax: Axes,
        timestamps: List[datetime],
        værdier: List[float],
        farve: str,
        label: str,
        marker: str = 'o'
    ) -> None:
        """
        Plotter en data serie på axes med styling.
        
        Args:
            ax: Matplotlib axes at plotte på
            timestamps: X-axis værdier (tid)
            værdier: Y-axis værdier (sensor målinger)
            farve: Hex color code (#RRGGBB)
            label: Legend label
            marker: Marker style ('o' = circle, 's' = square)
        
        Styling Parametrer:
            linewidth=2.5: Tyk nok til at være læsbar på skærmen
            markersize=3: for clean look (ikke dominerende)
            alpha=0.9: let gennemsigtig
        
        Marker Styles:
            'o': Circle (brugt til indendørs data)
            's': Square (brugt til udendørs data)
            Dette gør det lettere at differentiere selv uden farve
        
        Sideeffekter:
            Modificerer ax med ny plot linje
        
        Note:
            Styling parametre optimeret til 800x480 skærmy.
            Linewidth og markersize kan justeres for andre resolutions.
        """
        ax.plot(
            timestamps,
            værdier,
            color=farve,
            linewidth=2.5,
            label=label,
            marker=marker,
            markersize=3,
            alpha=0.9
        )
    
    def _stil_axes(
        self,
        ax: Axes,
        titel: str,
        y_label: str
    ) -> None:
        """
        Anvender styling til graf axes.
        
        Args:
            ax: Matplotlib axes at style
            titel: Graf overskrift
            y_label: Y-axis label (måleenhed)
        
        Styling Elements:
            Background: Næsten sort (#0a0a0a)
            Title: Bold, hvid, 22pt
            Axis labels: Grå (#888888), 14pt
            Grid: Dashed, transparent (alpha=0.2)
        
        Note:
            Font sizes optimeret til 800x480 skærm.
            22pt title er læsbar på afstand, 14pt labels virker fint
            Tingene bliver rettet til når vi skal teste design.
        """
        # Background color
        ax.set_facecolor('#0a0a0a')  # Næsten sort
        
        # Title styling
        ax.set_title(
            titel,
            fontsize=22,
            fontweight='bold',
            color='white',
            pad=20  # mellemrum mellem title og plot
        )
        
        # Axis labels
        ax.set_xlabel('Dato', fontsize=14, color='#888888')
        ax.set_ylabel(y_label, fontsize=14, color='#888888')
        
        # Grid for læsbarhed
        ax.grid(
            True,
            alpha=0.2,
            linestyle='--',
            linewidth=0.5
        )
    
    def _konfigurer_dato_akse(self, ax: Axes) -> None:
        """
        Konfigurerer X-axis til at vise datoer læsbart.
        
        Args:
            ax: Matplotlib axes at konfigurere
        
        Date Formatting:
            Format: DD/MM (f.eks. 16/12)
            Interval: Hver dag (DayLocator)
            Rotation: 45° for at undgå overlap
        
        Note:
            Bruger dansk locale hvis tilgængeligt (sat i modul init).
            Fallback til system default hvis dansk ikke tilgængeligt.
        """
        # Date formatting
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        
        # Tick interval - vis hver dag
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        
        # Rotation for læsbarhed
        plt.setp(
            ax.xaxis.get_majorticklabels(),
            rotation=45,
            ha='right'  # Horisontalt alignment
        )
    
    def _tilføj_legend(
        self,
        ax: Axes,
        har_data: bool
    ) -> None:
        """
        Tilføjer legend til graf hvis der er data.
        
        Args:
            ax: Matplotlib axes
            har_data: Om der er plottet noget data
        
        Legend Styling:
            Location: Upper left
            Framealpha: 0.8 (semi-transparent)
            Facecolor: Mørkegrå (#1a1a1a)
            Edgecolor: Lysegrå (#333333)
            Fontsize: 12pt
        
        Sideeffekter:
            Tilføjer styled legend til ax hvis har_data=True
            Ingen ændringer hvis har_data=False
        
        Note:
            Legend position 'upper left' virker bedst for sensor data der
            typisk stiger over tid (varme om dagen, kølig om natten).
        """
        if not har_data:
            return
        
        legend = ax.legend(
            loc='upper left',
            framealpha=0.8,
            facecolor='#1a1a1a',
            edgecolor='#333333',
            fontsize=12
        )
        
        # Sæt legend tekst farve til hvid
        plt.setp(legend.get_texts(), color='white')
    
    def generer_graf(
        self,
        data_type: str,
        dage: int = 7
    ) -> BytesIO:
        """
        Genererer matplotlib graf og returnerer som PNG i BytesIO.
        
        Dette er main entry point for graf generation. Håndterer hele flowet
        fra data hentning til PNG generation med error handling og cleanup.
        
        Args:
            data_type: Type af graf ('temperatur', 'luftfugtighed', 'gas')
            dage: Antal dage historik at vise (1-14, default 7)
        
        Returns:
            BytesIO objekt indeholdende PNG billede data
        
        Raises:
            ValueError: Hvis data_type ugyldig eller dage out of range
            RuntimeError: Hvis graf generation fejler
        
        Graf Generation Flow:
            1. Valider input parametre
            2. Hent data fra database
            3. Hvis ingen data -> generer placeholder graf
            4. Organiser data (indoor vs outdoor)
            5. Opret matplotlib figur og axes
            6. Plot indoor data
            7. Plot outdoor data (hvis relevant)
            8. Applicer styling og formatting
            9. Gem til BytesIO som PNG
            10. Cleanup (close figure)
        
        Memory Management:
            VIGTIGT: plt.close(fig) skal ALTID kaldes for at frigive memory.
            Matplotlib holder reference til alle figurer indtil explicit close,
            hvilket kan føre til memory leak ved gentagne calls.
        
        Eksempler:
            generator = GrafGenerator()
            billede = generator.generer_graf('temperatur', dage=7)
            len(billede.getvalue())  # PNG size i bytes
            
            # Stream direkte til HTTP response
            from fastapi.responses import StreamingResponse
            return StreamingResponse(billede, media_type="image/png")
        
        Note:
            Frontend roterer automatisk mellem graf typer hver 15. sekund ved
            at kalde denne endpoint med incrementing graf_type. Dette giver
            users en live dashboard effekt uden manuel navigation.
        """
        # Input validation
        self._valider_data_type(data_type)
        self._valider_dage(dage)
        
        # Hent data fra database
        try:
            data: List[Dict[str, Any]] = db.hent_datahistorik(
                data_type,
                dage
            )
        except Exception as fejl:
            db.gem_fejl(
                ENHEDS_ID,
                'GRAPH_GENERATOR',
                f"Database query fejlede: {fejl}"
            )
            # Fallback til placeholder graf
            return self._generer_ingen_data_graf(data_type)
        
        # Tjek for manglende data
        if not data:
            return self._generer_ingen_data_graf(data_type)
        
        # Organiser data
        indoor_data, outdoor_data = self._organiser_data(data)
        
        # Hent graf konfiguration
        config: Dict[str, Optional[str]] = self.configs[data_type]
        
        # Opret matplotlib figur
        try:
            # Figur size optimeret til 800x480 skærm
            fig: Figure
            ax: Axes
            fig, ax = plt.subplots(figsize=(12, 7), dpi=80)
            
            # Background colors
            fig.patch.set_facecolor('#000000')
            ax.set_facecolor('#0a0a0a')
            
            # Plot indoor data
            if indoor_data:
                timestamps = self._udvind_timestamps(indoor_data)
                værdier = self._udvind_værdier(indoor_data, data_type)
                
                self._plot_data_serie(
                    ax,
                    timestamps,
                    værdier,
                    farve=config['indoor_color'],
                    label=config['indoor_label'],
                    marker='o'
                )
            
            # Plot udendørs data (kun hvis relevant og data findes)
            if outdoor_data and config['outdoor_color']:
                timestamps = self._udvind_timestamps(outdoor_data)
                værdier = self._udvind_værdier(outdoor_data, data_type)
                
                self._plot_data_serie(
                    ax,
                    timestamps,
                    værdier,
                    farve=config['outdoor_color'],
                    label=config['outdoor_label'],
                    marker='s'
                )
            
            # Styling
            self._stil_axes(ax, config['title'], config['unit'])
            self._konfigurer_dato_akse(ax)
            self._tilføj_legend(ax, bool(indoor_data or outdoor_data))
            
            # Layout optimering
            plt.tight_layout()
            
            # Gem til BytesIO
            buf = BytesIO()
            plt.savefig(
                buf,
                format='png',
                facecolor='#000000',
                edgecolor='none',
                bbox_inches='tight'
            )
            buf.seek(0)  # Reset til start for læsning
            
            # Cleanup - for memory management
            plt.close(fig)
            
            return buf
            
        except Exception as fejl:
            # Cleanup ved fejl
            try:
                plt.close(fig)
            except:
                pass
            
            db.gem_fejl(
                ENHEDS_ID,
                'GRAPH_GENERATOR',
                f"Graf generation fejlede for {data_type}: {fejl}"
            )
            raise RuntimeError(f"Kunne ikke generere graf: {fejl}")
    
    def _generer_ingen_data_graf(self, data_type: str) -> BytesIO:
        """
        Genererer placeholder graf når der ingen data er.
        
        Args:
            data_type: Type af graf for titel
        
        Returns:
            BytesIO med placeholder PNG
        
        Placeholder Design:
            Sort baggrund
            Centered tekst: "Ingen data tilgængelig endnu\n\nVent venligst..."
            Ingen axes eller grid (clean look)
            Graf titel bevares (viser hvad der ventes på)
        
        Note:
            Viser user-friendly besked i stedet for blank graf eller fejl.
            Frontend polling fortsætter og vil vise data når det ankommer.
        """
        # Hent konfiguration
        config: Dict[str, Optional[str]] = self.configs[data_type]
        
        # Opret figur
        fig: Figure
        ax: Axes
        fig, ax = plt.subplots(figsize=(10, 6), dpi=80)
        
        # Background colors
        fig.patch.set_facecolor('#000000')
        ax.set_facecolor('#0a0a0a')
        
        # Placeholder tekst
        ax.text(
            0.5, 0.5,
            'Ingen data tilgængelig endnu\n\nVent venligst...',
            horizontalalignment='center',
            verticalalignment='center',
            transform=ax.transAxes,
            fontsize=24,
            color='#666666',
            fontweight='bold'
        )
        
        # Title
        ax.set_title(
            config['title'],
            fontsize=18,
            fontweight='bold',
            color='white',
            pad=20
        )
        
        # Fjern axes
        ax.set_xticks([])
        ax.set_yticks([])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_visible(False)
        ax.spines['left'].set_visible(False)
        
        # Layout
        plt.tight_layout()
        
        # Gem til BytesIO
        buf = BytesIO()
        plt.savefig(
            buf,
            format='png',
            facecolor='#000000',
            edgecolor='none',
            bbox_inches='tight'
        )
        buf.seek(0)
        
        # Cleanup
        plt.close(fig)
        
        return buf
    
    def generer_alle_grafer(
        self,
        dage: int = 7
    ) -> Dict[str, BytesIO]:
        """
        Genererer alle tre graf typer på én gang.
        
        Convenience metode til batch generation. Nyttig for pre-caching
        eller debugging af graf system.
        
        Args:
            dage: Antal dage historik (1-14)
        
        Returns:
            Dictionary med BytesIO objekter:
            {'temperatur': BytesIO, 'luftfugtighed': BytesIO, 'gas': BytesIO}
        
        Raises:
            ValueError: Hvis dage er "out of range"
        
        Error Handling:
            Hvis en enkelt graf fejler, genereres placeholder for den graf og
            vi fortsætter med de andre. Dette sikrer partial success i
            stedet for total failure.
        
        Eksempler:
            generator = GrafGenerator()
            grafer = generator.generer_alle_grafer(dage=7)
            for type, billede in grafer.items():
                print(f"{type}: {len(billede.getvalue())} bytes")
            temperatur: 198450 bytes
            luftfugtighed: 203120 bytes
            gas: 187890 bytes
        """
        # Valider dage en gang for alle
        self._valider_dage(dage)
        
        resultat: Dict[str, BytesIO] = {}
        
        for data_type in self.configs.keys():
            try:
                resultat[data_type] = self.generer_graf(data_type, dage)
            except Exception as fejl:
                db.gem_fejl(
                    ENHEDS_ID,
                    'GRAPH_GENERATOR',
                    f"Batch generation fejlede for {data_type}: {fejl}"
                )
                # Forsæt med andre grafer selvom en fejler
                resultat[data_type] = self._generer_ingen_data_graf(data_type)
        
        return resultat


# Global singleton instance
graf_generator: GrafGenerator = GrafGenerator()
"""
Global graf generator instance.

Denne singleton bruges af app.py til on-demand graf generation.
Sikrer konsistent styling på tværs af alle requests.

Singleton Pattern:
    I stedet for at oprette ny GrafGenerator per request, genbruges
    én global instance:
    
    from graph_generator import graf_generator
    
    Dette giver:
    - Memory efficiency: En enkelt konfiguration i memory
    - Konsistent styling: Samme farver hver gang
    - Performance: Ingen init overhead per request

Livscyklus:
    1. Import: Instance oprettes med matplotlib config
    2. Runtime: generer_graf() kaldes ved HTTP requests
    3. Shutdown: Ingen cleanup nødvendig

Thread Safety:
    Matplotlib er IKKE thread-safe, men da graf generation kaldes fra
    asyncio event loop (single-threaded), er der ingen race conditions.
    Hver generer_graf() call er isoleret med egen Figure instance.

Eksempler:
    from graph_generator import graf_generator
    billede = graf_generator.generer_graf('temperatur', dage=7)
    # Stream til HTTP response
    return StreamingResponse(billede, media_type="image/png")
"""