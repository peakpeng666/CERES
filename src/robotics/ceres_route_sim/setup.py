from glob import glob
from pathlib import Path
from setuptools import setup
setup(name='ceres_route_sim', version='0.1.0', packages=['ceres_route_sim'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/ceres_route_sim']),
                  ('share/ceres_route_sim', ['package.xml']),
                  *[(f'share/ceres_route_sim/{d}', sorted(path for path in glob(f'{d}/*') if Path(path).is_file()))
                    for d in ('config','launch','worlds')]],
      install_requires=['setuptools'], zip_safe=True,
      maintainer='CERES project', maintainer_email='noreply@example.invalid',
      description='Route display plus separate synthetic simulation', license='Apache-2.0',
      entry_points={'console_scripts': ['route_markers = ceres_route_sim.route_markers:main',
                                       'synthetic_smoke = ceres_route_sim.synthetic_smoke:main',
                                       'upstream_preflight = ceres_route_sim.upstream_core:main']})
