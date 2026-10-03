"""体积云渲染系统 - 存在bug"""
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


class VolumeCloud:
    def __init__(self):
        self.layers: list[CloudLayer] = [CloudLayer()]
        self.sun_position: tuple[float, float, float] = (0, 1, 0)
        self.time: float = 0.0
        self.weather_type: str = "clear"  # clear, cloudy, overcast, rain
        self._noise_seed: int = 42

    def _noise_2d(self, x: float, y: float) -> float:
        """简单2D噪声 - BUG: 实现不正确"""
        # BUG: 简单伪随机，不是真正的噪声
        return (math.sin(x * 12.9898 + y * 78.233) * 43758.5453) % 1.0

    def sample_cloud(self, x: float, y: float, z: float) -> float:
        """云采样 - BUG: 只做简单2D噪声不做3D体积采样"""
        # BUG: 只做2D噪声，不做3D体积采样
        layer = self.layers[0]
        # 简单2D噪声
        n = self._noise_2d(x * 0.01 + self.time * layer.speed * 0.001, y * 0.01)
        # BUG: 不做噪声分层
        return max(0, n - (1 - layer.coverage))

    def calculate_cloud_lighting(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """计算云光照 - BUG: 只做简单明暗"""
        # BUG: 不做散射和吸收
        density = self.sample_cloud(x, y, z)
        # 简单明暗
        brightness = 0.5 + density * 0.5
        return (brightness, brightness, brightness)

    def animate_clouds(self, dt: float):
        """云动画 - BUG: 只做整体平移不做形态变化"""
        self.time += dt
        # BUG: 只更新时间，不做形态变化
        # 云的形状永远不变

    def ray_march(self, ray_origin: tuple[float, float, float], ray_dir: tuple[float, float, float], max_steps: int = 32) -> tuple[float, float, float, float]:
        """光线步进 - BUG: 只做固定步数不做自适应"""
        # BUG: 固定步数，不做自适应，不做提前终止
        color = [0.0, 0.0, 0.0]
        alpha = 0.0
        t = 0.0
        step_size = 10.0
        for i in range(max_steps):
            pos = (
                ray_origin[0] + ray_dir[0] * t,
                ray_origin[1] + ray_dir[1] * t,
                ray_origin[2] + ray_dir[2] * t,
            )
            density = self.sample_cloud(*pos)
            if density > 0:
                lighting = self.calculate_cloud_lighting(*pos)
                for c in range(3):
                    color[c] += lighting[c] * density * (1 - alpha)
                alpha += density * 0.1
                alpha = min(1.0, alpha)
            # BUG: 不做提前终止
            t += step_size
        return (color[0], color[1], color[2], alpha)

    def update_weather(self, weather_type: str, transition_time: float = 5.0):
        """更新天气 - BUG: 只做云量不做其他属性"""
        # BUG: 只更新天气类型，不做过渡，不做对光照的影响
        self.weather_type = weather_type
        if weather_type == "clear":
            self.layers[0].coverage = 0.2
        elif weather_type == "cloudy":
            self.layers[0].coverage = 0.5
        elif weather_type == "overcast":
            self.layers[0].coverage = 0.9
        elif weather_type == "rain":
            self.layers[0].coverage = 0.95
