# Third-party notices for the collaborator images

Checked on 2026-10-19 against upstream project pages and the Gazebo Fuel API.
This notice covers the collaborator images built from `deploy/`. These images
install ROS packages from apt and exclude the Fuel cache and Fields2Cover wheel.
The older `setup/platform/` snapshot image is covered separately below.

## Published snapshot exception requiring review

As of 2026-10-19, the
[platform-snapshot-20261018-ddb5113e Release](https://github.com/peakpeng666/URP/releases/tag/platform-snapshot-20261018-ddb5113e)
still publishes `fuel-cache.tar.zst` (144,263,245 bytes). Its description lists
Fuel models and a Fields2Cover wheel. Those assets remain in the published Release.
Before further redistribution, check their licenses, attribution requirements,
whether the content is unchanged, and permitted uses. The exclusions below
apply to new collaborator images and asset uploads.

## Allowed inside the published images

| Piece | How it gets in | Terms | What we keep |
|---|---|---|---|
| ROS 2 Humble and Gazebo Fortress | apt from `packages.ros.org` and Ubuntu jammy | ROS 2 and Gazebo Fortress are Apache-2.0. Individual Ubuntu library packages keep their own licenses. | Installed as distribution packages. Source is the public apt repositories. |
| `ros-humble-foxglove-bridge` | `apt download` in `src/lab/ros/native/install-ros-extras.sh`, unpacked under the image's runtime prefix | MIT. Source: https://github.com/foxglove/ros-foxglove-bridge | Copyright and the MIT notice stay with the unpacked package. The image does not strip license files out of the deb. |
| `ros-humble-rosx-introspection` | same apt unpack | MPL-2.0. Source: https://github.com/facontidavide/rosx_introspection | The deb is redistributable when this notice and the public source offer stay available. License files from the package are not removed. |

## Excluded from new collaborator images and their asset uploads

| Piece | Terms seen on 2026-10-19 | Decision |
|---|---|---|
| OpenRobotics Warehouse, version 5 | CC BY-NC-ND 4.0 | Do not redistribute. A user may download it from https://fuel.gazebosim.org with `ceres fuel`. |
| MovAi shelf_big, version 2 | CC BY-NC-ND 4.0 | Do not redistribute. |
| MovAi shelf, version 1 | CC BY-NC 4.0 | Do not put it in an unrestricted public image or Release. |
| OpenRobotics Depot 6, Chair 3, foldable_chair 3, MaleVisitorOnPhone 2 | CC BY 4.0 | Redistribution would require attribution. They are not baked in. The warehouse world is incomplete without the NC/ND models, so a partial CC BY set is not shipped either. |
| OpenRobotics Table 4, Jersey Barrier 6; plateau Casual female 1 | CC0 1.0 | Redistribution is allowed. They are still not baked in, because they do not make warehouse or depot runnable by themselves. |
| Fields2Cover 2.1.0 wheel `fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl` | BSD-3-Clause. Source: https://github.com/Fields2Cover/Fields2Cover | The wheel is not in git and not in the image. The owner may upload it with `deploy/upload-assets.sh` only together with this notice and the BSD copyright and disclaimer. SHA256 `0fe53e459024b282be455b2a4bdbca3d93efa029539ff87c88c1105361344c19`. |

`maze` starts with the TurtleBot4 world that apt installs. It does not need Fuel.

The owner's snapshot `fuel-cache.tar.zst` (SHA256 `ee2d2ce72dea345b5aaac8c4fe0d3d501a8d51c3211ba1c038ca025cc0ae3b0d`) mixes the models above. Do not include that mixed tarball in new collaborator releases. The older
public snapshot listed above remains a separate review item.

## Desktop application

| Component | Source | Terms |
|---|---|---|
| Electron, electron-builder, electron-updater | npm, pinned in `src/desktop/package-lock.json` | MIT; Chromium and Node notices ship in the installer as `LICENSES.chromium.html` |
| CPython 3.12 (python-build-standalone) | astral-sh/python-build-standalone release used by uv | PSF licence and bundled-library notices in the runtime's `LICENSE.txt` |
| Python dependencies | `src/lab/uv.lock` | per package, carried in each `*.dist-info` |
| Microsoft Visual C++ runtime (Windows) | `msvc-runtime` wheel from PyPI | Microsoft redistributable licence; unmodified DLLs placed next to `python.exe` |
| Model catalog snapshot | [models.dev](https://models.dev), trimmed by `ceres_lab/agent/catalog.py` | MIT |
| MuJoCo | `mujoco` wheel from PyPI | Apache-2.0 |
| TurtleBot4 official maps (depot, maze, warehouse; see NOTICE.txt beside them) | `turtlebot4_navigation`, unmodified copies in `src/lab/ceres_lab/data/maps/` for machines without ROS | Apache-2.0 |
