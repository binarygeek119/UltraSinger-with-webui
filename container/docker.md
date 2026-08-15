# Containerized UltraSinger (Docker)

## Web UI (this fork)

From the **repository root** (not this `container/` folder):

```bash
docker compose pull
docker compose up
# CPU only:
# docker compose -f docker-compose.cpu.yml pull
# docker compose -f docker-compose.cpu.yml up
```

This pulls `ghcr.io/binarygeek119/ultrasinger-with-webui:latest` (no local image build). To build from this repo instead:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build
```

Then open http://localhost:8756

- Job data: `./data`
- Optional exports (enable in Settings): `./export/yarg` and `./export/ultrastar`
- Host port: `WEBUI_PORT=8756` (change if needed)
- NVIDIA GPU compose needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)

The image starts `python3.12 -m webui`. For the original CLI shell workflow, see below.

## CLI image (upstream-style)

To run the docker run `git clone https://github.com/rakuri255/UltraSinger.git`
enter the UltraSinger folder.
run this command to build the docker
`docker build -t ultrasinger .` make sure to include the "." at the end
let this run till complete.
then run this command
`docker run --gpus all -it --name UltraSinger -v  $pwd/src/output:/app/src/output ultrasinger`

Docker-Compose
- there are two files that you can pick from.
- cd into the `container` folder
- to download and setup, run either
  - `docker-compose -f compose-gpu.yml up` if you have an nvidia gpu to use with UltraSinger 
  - or`docker-compose -f compose-nogpu.yml up` if you wish to only use the CPU for UltraSinger

Output
by default the docker-compose will setup the output folder as `/output` inside the docker.
on the host machine it will map to the folder with the `compose<gpu|nogpu>.yml` file under `output`
you may change this by editing the `compose<gpu|nogpu>.yml`

to edit the file.
use any text editor you wish. i would recoment nano.
run `nano compose<gpu|nogpu>.yml`
then change this line
`            -  ./output:/app/UltraSinger/src/output`
to anything you line for on your host machine.
`            -  /yourfolderpathhere:/app/UltraSinger/src/output`
sample
`            -  /mnt/user/appdata/UltraSinger:/output`
note the blank space before the `-`
formating is important here in this file.

this will create and drop you into the docker.
now run this command.
`python3 UltraSinger.py -i file`
or
`python3 UltraSinger.py -i youtube_url`
to use mp3's in the folder you git cloned you must place all songs you like in UltraSinger/src/output.
this will be the place for youtube links aswell.


to quit the docker just type exit.

to reenter docker run this command
`docker start UltraSinger && Docker exec -it UltraSinger /bin/bash`
