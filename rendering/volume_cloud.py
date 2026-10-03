"""体积云渲染系统"""
from dataclasses import dataclass, field
from typing import Optional
import math
import random


@dataclass
class CloudLayer:
    altitude: float = 1000.0
    thickness: float = 500.0
    coverage: float = 0.5
    speed: float = 10.0
    direction: tuple[float, float] = (1, 0)


# 天气预设：多属性（云量、密度、光照强度、风速、湿度）
WEATHER_PRESETS: dict[str, dict[str, float]] = {
    "clear":    {"coverage": 0.20, "density": 0.6, "light": 1.00, "wind": 5.0,  "humidity": 0.3},
    "cloudy":   {"coverage": 0.50, "density": 1.0, "light": 0.80, "wind": 10.0, "humidity": 0.6},
    "overcast": {"coverage": 0.90, "density": 1.4, "light": 0.50, "wind": 15.0, "humidity": 0.8},
    "rain":     {"coverage": 0.95, "density": 1.8, "light": 0.35, "wind": 20.0, "humidity": 1.0},
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


class VolumeCloud:
    def __init__(self):
        self.layers: list[CloudLayer] = [CloudLayer()]
        self.sun_position: tuple[float, float, float] = (0, 1, 0)
        self.time: float = 0.0
        self.weather_type: str = "clear"  # clear, cloudy, overcast, rain
        self._noise_seed: int = 42
        # 形态演化与生成/消散
        self._morph_time: float = 0.0
        self._lifecycle: float = 1.0
        self._rng = random.Random(42)
        # 天气过渡状态
        self._weather_current: dict[str, float] = dict(WEATHER_PRESETS["clear"])
        self._weather_start: dict[str, float] = dict(WEATHER_PRESETS["clear"])
        self._weather_target: dict[str, float] = dict(WEATHER_PRESETS["clear"])
        self._transition_elapsed: float = 0.0
        self._transition_time: float = 0.0

    # ---------- 3D 噪声 ----------

    def _hash3(self, ix: int, iy: int, iz: int) -> float:
        n = (ix * 374761393 + iy * 668265263 + iz * 1442695041
             + self._noise_seed * 975313) & 0xFFFFFFFF
        n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
        n = n ^ (n >> 16)
        return n / 0xFFFFFFFF

    def _noise_3d(self, x: float, y: float, z: float) -> float:
        """3D 值噪声（三线性插值）"""
        xi, yi, zi = math.floor(x), math.floor(y), math.floor(z)
        xf, yf, zf = x - xi, y - yi, z - zi
        u, v, w = _smoothstep(xf), _smoothstep(yf), _smoothstep(zf)
        c000 = self._hash3(xi, yi, zi)
        c100 = self._hash3(xi + 1, yi, zi)
        c010 = self._hash3(xi, yi + 1, zi)
        c110 = self._hash3(xi + 1, yi + 1, zi)
        c001 = self._hash3(xi, yi, zi + 1)
        c101 = self._hash3(xi + 1, yi, zi + 1)
        c011 = self._hash3(xi, yi + 1, zi + 1)
        c111 = self._hash3(xi + 1, yi + 1, zi + 1)
        x00 = _lerp(c000, c100, u)
        x10 = _lerp(c010, c110, u)
        x01 = _lerp(c001, c101, u)
        x11 = _lerp(c011, c111, u)
        y0 = _lerp(x00, x10, v)
        y1 = _lerp(x01, x11, v)
        return _lerp(y0, y1, w)

    def _fbm(self, x: float, y: float, z: float, octaves: int = 4) -> float:
        """分形噪声：低频形状 + 高频细节分层叠加"""
        total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
        for _ in range(octaves):
            total += self._noise_3d(x * freq, y * freq, z * freq) * amp
            norm += amp
            amp *= 0.5
            freq *= 2.0
        return total / norm

    # ---------- 天气 ----------

    def _advance_transition(self, dt: float):
        if self._transition_time <= 0:
            self._weather_current = dict(self._weather_target)
        else:
            self._transition_elapsed = min(
                self._transition_time, self._transition_elapsed + dt)
            p = _smoothstep(self._transition_elapsed / self._transition_time)
            for key in self._weather_current:
                self._weather_current[key] = _lerp(
                    self._weather_start[key], self._weather_target[key], p)
        layer = self.layers[0]
        layer.coverage = self._weather_current["coverage"]
        layer.speed = self._weather_current["wind"]

    def update_weather(self, weather_type: str, transition_time: float = 5.0):
        """更新天气：多属性（云量/密度/光照/风速/湿度）并随时间平滑过渡"""
        if weather_type not in WEATHER_PRESETS:
            raise ValueError(f"未知天气类型: {weather_type}")
        self.weather_type = weather_type
        self._weather_start = dict(self._weather_current)
        self._weather_target = dict(WEATHER_PRESETS[weather_type])
        self._transition_time = max(0.0, transition_time)
        self._transition_elapsed = 0.0
        if self._transition_time <= 0:
            self._advance_transition(0.0)
        else:
            # 立即响应一部分，剩余部分由 animate_clouds 随时间过渡
            self._advance_transition(self._transition_time * 0.25)

    # ---------- 云采样 ----------

    def sample_cloud(self, x: float, y: float, z: float) -> float:
        """3D 体积采样：分层噪声 + 云层厚度分布 + 天气类型影响"""
        layer = self.layers[0]
        props = self._weather_current
        # 风场平移：风速影响移动速度
        wind_t = self.time * layer.speed * 0.01
        wx = x + wind_t * layer.direction[0]
        wz = z + wind_t * layer.direction[1]
        # 噪声分层：低频定形状，高频加细节
        base = self._fbm(wx * 0.002, y * 0.002, wz * 0.002, octaves=4)
        detail = self._fbm(wx * 0.01, y * 0.01,
                           wz * 0.01 + self._morph_time * 0.05, octaves=2)
        n = base * 0.7 + detail * 0.3
        # 形态变化：噪声场随时间演化，云的形状会慢慢改变
        morph = self._noise_3d(x * 0.001, self._morph_time * 0.1, z * 0.001)
        n = n * 0.8 + morph * 0.2
        # 云层厚度：垂直方向（z 为高度轴）上的密度分布
        half = max(layer.thickness * 0.5, 1e-6)
        dz = abs(z - layer.altitude) / half
        vertical = 1.0 - dz * dz if dz < 1.0 else 0.0
        # 天气类型影响云量与密度；生成/消散因子改变云的总量
        coverage = min(1.0, layer.coverage * self._lifecycle)
        density = max(0.0, n - (1.0 - coverage)) + coverage * 0.1
        return density * vertical * props["density"]

    # ---------- 云光照 ----------

    def calculate_cloud_lighting(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """云光照：散射 + 吸收 + 太阳方向 + 日出日落颜色渐变"""
        props = self._weather_current
        layer = self.layers[0]
        density = self.sample_cloud(x, y, z)
        sx, sy, sz = self.sun_position
        norm = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        sun_dir = (sx / norm, sy / norm, sz / norm)
        elevation = max(0.0, min(1.0, sun_dir[1]))
        # 颜色渐变：太阳接近地平线时偏红
        sunset = max(0.0, 1.0 - elevation / 0.5)
        sun_color = (1.0, 1.0 - 0.45 * sunset, 1.0 - 0.75 * sunset)
        # 太阳方向：沿太阳方向采样，估算光穿过云体的透射率
        s = layer.thickness * 0.25
        d_sun = self.sample_cloud(
            x + sun_dir[0] * s, y + sun_dir[1] * s, z + sun_dir[2] * s)
        # 散射与吸收（比尔-朗伯定律）
        sigma_a = 0.3 * props["density"]  # 吸收系数
        sigma_s = 0.8 * props["density"]  # 散射系数
        transmittance = math.exp(-(sigma_a + sigma_s) * d_sun * s * 0.01)
        # 边缘（密度低）透光多更亮，中心（密度高）吸收多更暗
        scatter = density * sigma_s * transmittance * 0.5
        ambient = 0.4
        # 天气对光照的影响：阴天/雨天整体变暗
        light = props["light"] * (0.3 + 0.7 * elevation)
        return tuple((ambient + scatter) * sun_color[c] * light
                     for c in range(3))

    # ---------- 云动画 ----------

    def animate_clouds(self, dt: float):
        """云动画：形态变化 + 风速影响 + 云的生成与消散"""
        self.time += dt
        # 形态变化：噪声场随时间演化（sample_cloud 中使用 _morph_time）
        self._morph_time += dt
        # 天气过渡随时间推进
        self._advance_transition(dt)
        # 云的生成与消散：生命周期因子随机游走，云的总量随时间增减
        self._lifecycle += (self._rng.random() - 0.5) * 0.1 * dt
        self._lifecycle = max(0.5, min(1.5, self._lifecycle))

    # ---------- 光线步进 ----------

    def ray_march(self, ray_origin: tuple[float, float, float],
                  ray_dir: tuple[float, float, float],
                  max_steps: int = 32,
                  max_distance: float = 5000.0,
                  max_depth: Optional[float] = None
                  ) -> tuple[float, float, float, float]:
        """光线步进：自适应步长 + 提前终止 + 深度测试

        max_depth: 地形深度（沿光线的距离），云被地形挡住时停止渲染。
        """
        color = [0.0, 0.0, 0.0]
        transmittance = 1.0
        t = 0.0
        base_step = max_distance / max_steps
        for _ in range(max_steps):
            # 深度测试：光线被地形遮挡，停止步进
            if max_depth is not None and t > max_depth:
                break
            # 自适应步长：近处步长小保证细节，远处步长大节省开销
            step = base_step * (1.0 + 2.0 * t / max_distance)
            pos = (ray_origin[0] + ray_dir[0] * t,
                   ray_origin[1] + ray_dir[1] * t,
                   ray_origin[2] + ray_dir[2] * t)
            density = self.sample_cloud(*pos)
            if density <= 1e-3:
                # 完全透明的体素：大步跳过，不累积
                t += step * 2.0
                continue
            lighting = self.calculate_cloud_lighting(*pos)
            alpha_step = 1.0 - math.exp(-density * step * 0.02)
            for c in range(3):
                color[c] += lighting[c] * alpha_step * transmittance
            transmittance *= 1.0 - alpha_step
            # 提前终止：已经足够不透明，无需继续步进
            if transmittance < 0.01:
                break
            t += step
        alpha = 1.0 - transmittance
        return (color[0], color[1], color[2], alpha)
