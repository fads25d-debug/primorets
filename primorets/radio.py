"""One-way free-space Friis link budget; antenna gains are specified in dBi."""
from dataclasses import asdict, dataclass
import math

C_M_S = 299792458.0


@dataclass(frozen=True)
class RadioSettings:
    frequency_ghz: float = 2.0
    tx_power_w: float = 10.0
    tx_gain_dbi: float = 30.0
    rx_gain_dbi: float = 30.0
    losses_db: float = 0.0
    threshold_dbm: float = -110.0

    def __post_init__(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("Параметры радиолинии должны быть конечными числами")
        if not 0.001 <= self.frequency_ghz <= 300 or not 1e-9 <= self.tx_power_w <= 1e9:
            raise ValueError("Частота: 0,001…300 ГГц; мощность: 10⁻⁹…10⁹ Вт")
        if not all(-100 <= v <= 100 for v in (self.tx_gain_dbi, self.rx_gain_dbi)):
            raise ValueError("Усиления антенн: −100…100 dBi")
        if not 0 <= self.losses_db <= 300 or not -300 <= self.threshold_dbm <= 100:
            raise ValueError("Дополнительные потери: 0…300 дБ; порог: −300…100 dBm")

    def at_range(self, range_km):
        if not math.isfinite(range_km) or range_km <= 0:
            raise ValueError("Наклонная дальность должна быть положительной и конечной")
        wavelength = C_M_S / (self.frequency_ghz * 1e9)
        # Compute logarithmically to preserve precision over satellite distances.
        fspl = 20 * (math.log10(4 * math.pi) + math.log10(range_km)
                     + 3 - math.log10(wavelength))
        if fspl < 0:
            raise ValueError("Дальность слишком мала для модели свободного пространства")
        received = (10 * math.log10(self.tx_power_w) + 30 + self.tx_gain_dbi
                    + self.rx_gain_dbi - fspl - self.losses_db)
        return dict(range_km=range_km, wavelength_m=wavelength, fspl_db=fspl,
                    free_space_power_factor=10 ** (-fspl / 10),
                    received_dbm=received, received_w=10 ** ((received - 30) / 10),
                    margin_db=received - self.threshold_dbm)

