"""体积云渲染系统"""
from dataclasses import dataclass
from typing import Callable, Optional
import math
import random


@dataclass
class CloudLayer:
    altitude: float = 1000.0      # 云层中心高度
    thickness: float = 500.0      # 云层厚度
    coverage: float = 0.5
    speed: float = 10.0
    direction: tuple[float, float] = (1, 0)


# 天气预设：云量 / 密度倍率 / 光照倍率 / 环境光强度
WEATHER_PRESETS: dict[str, dict[str, float]] = {
    "clear":    {"coverage": 0.20, "density_scale": 0.8, "light_scale": 1.00, "ambient": 0.35},
    "cloudy":   {"coverage": 0.50, "density_scale": 1.0, "light_scale": 0.80, "ambient": 0.30},
    "overcast": {"coverage": 0.90, "density_scale": 1.2, "light_scale": 0.55, "ambient": 0.25},
    "rain":     {"coverage": 0.95, "density_scale": 1.4, "light_scale": 0.40, "ambient": 0.20},
}


class VolumeCloud:
    def __init__(self):
        self.layers: list[CloudLayer] = [CloudLayer()]
        # 太阳位置，y 分量为高度（世界系中 z 轴向上）
        self.sun_position: tuple[float, float, float] = (0, 1, 0)
        self.time: float = 0.0
        self.weather_type: str = "clear"  # clear, cloudy, overcast, rain
        self._noise_seed: int = 42
        self._evolution: float = 0.0       # 形态演化时间
        self._rng = random.Random(self._noise_seed)
        self._cells: list[dict] = []       # 云朵生命周期（生成/消散）
        # 地形高度函数（用于深度测试），签名为 fn(x, y) -> height
        self.terrain_height_fn: Optional[Callable[[float, float], float]] = None
        # 天气状态与过渡
        self._weather_attrs: dict[str, float] = dict(WEATHER_PRESETS["clear"])
        self._weather_start: dict[str, float] = dict(self._weather_attrs)
        self._weather_target: dict[str, float] = dict(self._weather_attrs)
        self._transition_time: float = 0.0
        self._transition_elapsed: float = 0.0
        self._apply_weather_to_layers()

    # ---------- 噪声 ----------

    def _hash_3d(self, xi: int, yi: int, zi: int) -> float:
        n = (xi * 374761393 + yi * 668265263 + zi * 1440662683
             + self._noise_seed * 1013904223) & 0xFFFFFFFF
        n = (n ^ (n >> 13)) * 1274126177 & 0xFFFFFFFF
        n ^= n >> 16
        return n / 0xFFFFFFFF

    @staticmethod
    def _smooth(t: float) -> float:
        return t * t * (3.0 - 2.0 * t)

    def _noise_3d(self, x: float, y: float, z: float) -> float:
        """3D 值噪声（三线性插值）"""
        xi, yi, zi = math.floor(x), math.floor(y), math.floor(z)
        u = self._smooth(x - xi)
        v = self._smooth(y - yi)
        w = self._smooth(z - zi)

        def lerp(a: float, b: float, t: float) -> float:
            return a + (b - a) * t

        c00 = lerp(self._hash_3d(xi, yi, zi), self._hash_3d(xi + 1, yi, zi), u)
        c10 = lerp(self._hash_3d(xi, yi + 1, zi), self._hash_3d(xi + 1, yi + 1, zi), u)
        c01 = lerp(self._hash_3d(xi, yi, zi + 1), self._hash_3d(xi + 1, yi, zi + 1), u)
        c11 = lerp(self._hash_3d(xi, yi + 1, zi + 1), self._hash_3d(xi + 1, yi + 1, zi + 1), u)
        return lerp(lerp(c00, c10, v), lerp(c01, c11, v), w)

    def _fbm_3d(self, x: float, y: float, z: float, octaves: int = 4,
                lacunarity: float = 2.0, gain: float = 0.5) -> float:
        """分形噪声：多个倍频叠加，归一化到 [0, 1]"""
        total = 0.0
        amplitude = 1.0
        frequency = 1.0
        norm = 0.0
        for _ in range(octaves):
            total += amplitude * self._noise_3d(x * frequency, y * frequency, z * frequency)
            norm += amplitude
            amplitude *= gain
            frequency *= lacunarity
        return total / norm

    def _cloud_noise(self, x: float, y: float, z: float) -> float:
        """风场平移 + 形态演化 + 噪声分层（低频形状 + 高频细节）"""
        layer = self.layers[0]
        # 风驱动的平移：风速越大移动越快
        wx = x - layer.direction[0] * layer.speed * self.time
        wy = y - layer.direction[1] * layer.speed * self.time
        # 形态演化：噪声域随时间变化，云的形状会改变
        ez = z + self._evolution * 8.0
        base = self._fbm_3d(wx * 0.004, wy * 0.004, ez * 0.004, octaves=4)
        detail = self._fbm_3d(wx * 0.02 + 31.7, wy * 0.02 + 17.3, ez * 0.02, octaves=3)
        return base * 0.7 + detail * 0.3

    # ---------- 云采样 ----------

    def sample_cloud(self, x: float, y: float, z: float) -> float:
        """3D 体积采样：高度衰减 + 分层噪声 + 天气类型 + 云朵生命周期"""
        layer = self.layers[0]
        bottom = layer.altitude - layer.thickness * 0.5
        h = (z - bottom) / max(layer.thickness, 1e-6)
        if h <= 0.0 or h >= 1.0:
            return 0.0
        # 高度衰减：云的底部和顶部稀薄，中间最厚（体积厚度感）
        height_profile = 4.0 * h * (1.0 - h)
        coverage = self._weather_attrs["coverage"]
        n = self._cloud_noise(x, y, z)
        density = max(0.0, n - (1.0 - coverage)) / max(coverage, 1e-3)
        density *= height_profile * self._weather_attrs["density_scale"]
        density += self._cells_density(x, y, z)
        return min(1.0, density)

    # ---------- 光照 ----------

    def _sun_direction(self) -> tuple[float, float, float]:
        sx, sy, sz = self.sun_position
        length = math.sqrt(sx * sx + sy * sy + sz * sz) or 1.0
        # sun_position 的 y 是高度；世界系 z 轴向上
        return (sx / length, sz / length, sy / length)

    def _sun_tint(self) -> tuple[float, float, float]:
        """太阳颜色渐变：接近地平线时偏红（日出/日落）"""
        elevation = max(0.0, min(1.0, self._sun_direction()[2]))
        sunset = 1.0 - min(1.0, elevation * 2.5)
        return (1.0, 1.0 - 0.55 * sunset, 1.0 - 0.80 * sunset)

    def calculate_cloud_lighting(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """云光照：散射 + 吸收 + 太阳方向 + 颜色渐变"""
        sun_dir = self._sun_direction()
        tint = self._sun_tint()
        light_scale = self._weather_attrs["light_scale"]
        ambient_strength = self._weather_attrs["ambient"]
        density = self.sample_cloud(x, y, z)

        # 朝太阳方向步进，Beer-Lambert 吸收计算透射率
        optical = 0.0
        march_step = 40.0
        for i in range(1, 5):
            optical += self.sample_cloud(
                x + sun_dir[0] * march_step * i,
                y + sun_dir[1] * march_step * i,
                z + sun_dir[2] * march_step * i,
            ) * march_step
        transmittance = math.exp(-optical * 0.02)

        # 密度梯度估计法线 → 方向性散射（边缘亮、中心暗）
        eps = 5.0
        gx = self._cloud_noise(x + eps, y, z) - self._cloud_noise(x - eps, y, z)
        gy = self._cloud_noise(x, y + eps, z) - self._cloud_noise(x, y - eps, z)
        gz = self._cloud_noise(x, y, z + eps) - self._cloud_noise(x, y, z - eps)
        gl = math.sqrt(gx * gx + gy * gy + gz * gz) or 1e-9
        diffuse = max(0.0, (-gx / gl) * sun_dir[0] + (-gy / gl) * sun_dir[1] + (-gz / gl) * sun_dir[2])

        scatter = transmittance * (0.4 + 0.6 * diffuse)
        absorption = math.exp(-density * 2.0)  # 云中心吸收更强、更暗

        result = []
        for c in range(3):
            sun_term = tint[c] * scatter * light_scale
            ambient_term = ambient_strength * (0.5 + 0.5 * tint[c])
            value = (sun_term + ambient_term) * (0.4 + 0.6 * absorption)
            result.append(min(1.0, value))
        return (result[0], result[1], result[2])

    # ---------- 动画 ----------

    def animate_clouds(self, dt: float):
        """云动画：形态演化 + 风速平移 + 云朵生成/消散 + 天气过渡"""
        self.time += dt
        self._evolution += dt * 0.05
        self._advance_weather_transition(dt)
        self._update_cloud_cells(dt)

    def _update_cloud_cells(self, dt: float):
        """云朵生命周期：随风移动、生成、消散，数量随天气覆盖率变化"""
        layer = self.layers[0]
        for cell in self._cells:
            cell["age"] += dt
            cell["x"] += layer.direction[0] * layer.speed * dt
            cell["y"] += layer.direction[1] * layer.speed * dt
        self._cells = [c for c in self._cells if c["age"] < c["lifetime"]]
        target = int(round(self._weather_attrs["coverage"] * 6))
        while len(self._cells) < target:
            self._cells.append({
                "x": self._rng.uniform(-2000.0, 2000.0),
                "y": self._rng.uniform(-2000.0, 2000.0),
                "z": layer.altitude + self._rng.uniform(-0.3, 0.3) * layer.thickness,
                "radius": self._rng.uniform(150.0, 400.0),
                "strength": self._rng.uniform(0.1, 0.3),
                "age": 0.0,
                "lifetime": self._rng.uniform(120.0, 600.0),
            })

    def _cells_density(self, x: float, y: float, z: float) -> float:
        total = 0.0
        for cell in self._cells:
            dx, dy, dz = x - cell["x"], y - cell["y"], z - cell["z"]
            d2 = dx * dx + dy * dy + dz * dz
            r = cell["radius"]
            if d2 < r * r:
                falloff = 1.0 - math.sqrt(d2) / r
                # 生命周期：生成时增强，消散时减弱
                life = math.sin(math.pi * min(1.0, cell["age"] / cell["lifetime"]))
                total += cell["strength"] * falloff * life
        return total

    # ---------- 光线步进 ----------

    def get_terrain_height(self, x: float, y: float) -> float:
        if self.terrain_height_fn is not None:
            return self.terrain_height_fn(x, y)
        return 0.0

    def ray_march(self, ray_origin: tuple[float, float, float],
                  ray_dir: tuple[float, float, float],
                  max_steps: int = 32,
                  max_distance: float = 5000.0) -> tuple[float, float, float, float]:
        """光线步进：自适应步长 + 提前终止 + 地形深度测试"""
        length = math.sqrt(sum(c * c for c in ray_dir)) or 1.0
        dx, dy, dz = ray_dir[0] / length, ray_dir[1] / length, ray_dir[2] / length
        color = [0.0, 0.0, 0.0]
        alpha = 0.0
        t = 0.0
        for _ in range(max_steps):
            if alpha >= 0.98:      # 提前终止：几乎不透明
                break
            if t > max_distance:
                break
            px = ray_origin[0] + dx * t
            py = ray_origin[1] + dy * t
            pz = ray_origin[2] + dz * t
            # 深度测试：光线进入地形以下，云被遮挡，停止渲染
            if pz < self.get_terrain_height(px, py):
                break
            density = self.sample_cloud(px, py, pz)
            # 自适应步长：近处步长小、远处步长大
            step = 5.0 * (1.0 + 6.0 * t / max_distance)
            if density <= 0.0:
                t += step * 2.0    # 完全透明的区域加速跳过
                continue
            lighting = self.calculate_cloud_lighting(px, py, pz)
            a = 1.0 - math.exp(-density * step * 0.05)
            for c in range(3):
                color[c] += lighting[c] * a * (1.0 - alpha)
            alpha += a * (1.0 - alpha)
            t += step
        return (color[0], color[1], color[2], min(1.0, alpha))

    # ---------- 天气 ----------

    def update_weather(self, weather_type: str, transition_time: float = 5.0):
        """更新天气：多属性（云量/密度/光照/环境光）+ 平滑过渡"""
        if weather_type not in WEATHER_PRESETS:
            raise ValueError(f"未知天气类型: {weather_type}")
        self.weather_type = weather_type
        self._weather_start = dict(self._weather_attrs)
        self._weather_target = dict(WEATHER_PRESETS[weather_type])
        self._transition_time = max(0.0, transition_time)
        self._transition_elapsed = 0.0
        if self._transition_time == 0.0:
            self._weather_attrs = dict(self._weather_target)
            self._apply_weather_to_layers()

    def _advance_weather_transition(self, dt: float):
        if self._transition_time <= 0.0:
            return
        self._transition_elapsed = min(self._transition_time,
                                       self._transition_elapsed + dt)
        t = self._transition_elapsed / self._transition_time
        t = t * t * (3.0 - 2.0 * t)  # smoothstep 过渡
        for key in self._weather_attrs:
            self._weather_attrs[key] = (self._weather_start[key]
                                        + (self._weather_target[key] - self._weather_start[key]) * t)
        self._apply_weather_to_layers()

    def _apply_weather_to_layers(self):
        self.layers[0].coverage = self._weather_attrs["coverage"]
