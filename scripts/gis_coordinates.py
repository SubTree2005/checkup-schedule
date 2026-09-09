"""Explicit approximate BD-09LL chain; never relabel input coordinates as WGS84.

The inverse formula is an engineering approximation, NOT an official Baidu
inverse or a surveyed transformation. Numerical round-trip convergence does
not establish geographic accuracy. Only valid for this mainland China site.
"""
from math import atan2, cos, pi, sin, sqrt


def bd09_to_gcj02(lon, lat):
    x, y = lon - 0.0065, lat - 0.006
    x_pi = pi * 3000 / 180
    z = sqrt(x*x+y*y) - 0.00002*sin(y*x_pi)
    theta = atan2(y, x) - 0.000003*cos(x*x_pi)
    return z*cos(theta), z*sin(theta)


def wgs84_to_gcj02(lon, lat):
    x, y = lon-105, lat-35
    dlat = -100+2*x+3*y+0.2*y*y+0.1*x*y+0.2*sqrt(abs(x))
    dlon = 300+x+2*y+0.1*x*x+0.1*x*y+0.1*sqrt(abs(x))
    common = (20*sin(6*x*pi)+20*sin(2*x*pi))*2/3
    dlat += common + (20*sin(y*pi)+40*sin(y*pi/3))*2/3
    dlat += (160*sin(y*pi/12)+320*sin(y*pi/30))*2/3
    dlon += common + (20*sin(x*pi)+40*sin(x*pi/3))*2/3
    dlon += (150*sin(x*pi/12)+300*sin(x*pi/30))*2/3
    a, ee, rad = 6378245., 0.006693421622965943, lat*pi/180
    magic = 1-ee*sin(rad)**2
    dlat *= 180/((a*(1-ee)/(magic*sqrt(magic)))*pi)
    dlon *= 180/(a/sqrt(magic)*cos(rad)*pi)
    return lon+dlon, lat+dlat


def gcj02_to_wgs84(lon, lat):
    guess = [lon, lat]
    for _ in range(10):
        projected = wgs84_to_gcj02(*guess)
        guess[0] -= projected[0]-lon
        guess[1] -= projected[1]-lat
    return guess
