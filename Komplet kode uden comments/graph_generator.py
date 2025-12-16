

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.axes import Axes
from datetime import datetime, timedelta
from io import BytesIO
from typing import Dict, List, Optional, Any, Tuple
from database import db
from config import ENHEDS_ID
import locale

try:
    locale.setlocale(locale.LC_TIME, 'da_DK.UTF-8')
except locale.Error as fejl:
    db.gem_fejl(
        ENHEDS_ID,
        'GRAPH_GENERATOR',
        f"Kunne ikke sætte dansk locale: {fejl}. Bruger system default."
    )


class GrafGenerator:

    
    def __init__(self) -> None:

        plt.style.use('dark_background')
        
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
                'outdoor_color': None,
                'indoor_label': 'Gas Modstand',
                'outdoor_label': None
            }
        }
    
    def _valider_data_type(self, data_type: str) -> None:

        if data_type not in self.configs:
            gyldige_typer = ', '.join(self.configs.keys())
            raise ValueError(
                f"Ugyldig data type: '{data_type}'. "
                f"Gyldige typer: {gyldige_typer}"
            )
    
    def _valider_dage(self, dage: int) -> None:

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

            indoor_data: List[Dict[str, Any]] = []
            outdoor_data: List[Dict[str, Any]] = []
            
            outdoor_identifiers = ['ESP32', 'DHT', 'OUTDOOR', 'UDE', 'UDENFOR']
            
            for d in data:
                kilde_upper = d['kilde'].upper()
                
                if 'BME680' in kilde_upper:
                    indoor_data.append(d)
                elif any(identifier in kilde_upper for identifier in outdoor_identifiers):
                    outdoor_data.append(d)
                else:
                    print(f"Ukendt sensor kilde: {d['kilde']}")
            
            return indoor_data, outdoor_data
    
    def _konverter_gas_til_kiloohm(
        self,
        data: List[Dict[str, Any]]
    ) -> List[float]:

        return [d['værdi'] / 1000.0 for d in data]
    
    def _udvind_timestamps(
        self,
        data: List[Dict[str, Any]]
    ) -> List[datetime]:

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

        ax.set_facecolor('#0a0a0a')
        
        ax.set_title(
            titel,
            fontsize=22,
            fontweight='bold',
            color='white',
            pad=20
        )
        
        ax.set_xlabel('Dato', fontsize=14, color='#888888')
        ax.set_ylabel(y_label, fontsize=14, color='#888888')
        
        ax.grid(
            True,
            alpha=0.2,
            linestyle='--',
            linewidth=0.5
        )
    
    def _konfigurer_dato_akse(self, ax: Axes) -> None:

        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d/%m'))
        
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        
        plt.setp(
            ax.xaxis.get_majorticklabels(),
            rotation=45,
            ha='right'
        )
    
    def _tilføj_legend(
        self,
        ax: Axes,
        har_data: bool
    ) -> None:

        if not har_data:
            return
        
        legend = ax.legend(
            loc='upper left',
            framealpha=0.8,
            facecolor='#1a1a1a',
            edgecolor='#333333',
            fontsize=12
        )
        
        plt.setp(legend.get_texts(), color='white')
    
    def generer_graf(
        self,
        data_type: str,
        dage: int = 7
    ) -> BytesIO:

        self._valider_data_type(data_type)
        self._valider_dage(dage)
        
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
            return self._generer_ingen_data_graf(data_type)
        
        if not data:
            return self._generer_ingen_data_graf(data_type)
        
        indoor_data, outdoor_data = self._organiser_data(data)
        
        config: Dict[str, Optional[str]] = self.configs[data_type]
        
        try:
            fig: Figure
            ax: Axes
            fig, ax = plt.subplots(figsize=(12, 7), dpi=80)
            
            fig.patch.set_facecolor('#000000')
            ax.set_facecolor('#0a0a0a')
            
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
            
            self._stil_axes(ax, config['title'], config['unit'])
            self._konfigurer_dato_akse(ax)
            self._tilføj_legend(ax, bool(indoor_data or outdoor_data))
            
            plt.tight_layout()
            
            buf = BytesIO()
            plt.savefig(
                buf,
                format='png',
                facecolor='#000000',
                edgecolor='none',
                bbox_inches='tight'
            )
            buf.seek(0)
            
            plt.close(fig)
            
            return buf
            
        except Exception as fejl:
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

        config: Dict[str, Optional[str]] = self.configs[data_type]
        
        fig: Figure
        ax: Axes
        fig, ax = plt.subplots(figsize=(10, 6), dpi=80)
        
        fig.patch.set_facecolor('#000000')
        ax.set_facecolor('#0a0a0a')
        
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
        
        ax.set_title(
            config['title'],
            fontsize=18,
            fontweight='bold',
            color='white',
            pad=20
        )
        
        ax.set_xticks([])
        ax.set_yticks([])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_visible(False)
        ax.spines['left'].set_visible(False)
        
        plt.tight_layout()
        
        buf = BytesIO()
        plt.savefig(
            buf,
            format='png',
            facecolor='#000000',
            edgecolor='none',
            bbox_inches='tight'
        )
        buf.seek(0)
        
        plt.close(fig)
        
        return buf
    
    def generer_alle_grafer(
        self,
        dage: int = 7
    ) -> Dict[str, BytesIO]:

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
                resultat[data_type] = self._generer_ingen_data_graf(data_type)
        
        return resultat


graf_generator: GrafGenerator = GrafGenerator()
