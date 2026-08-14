"""Punto de entrada: ``python -m gesture_control``."""

from __future__ import annotations

import argparse
import logging
import sys

from . import config as config_module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gesture_control",
        description="Control de Windows mediante gestos de la mano (MediaPipe Tasks).",
    )
    parser.add_argument("-c", "--config", help="Ruta a un config.yaml alternativo")
    parser.add_argument("--camera", type=int, help="Índice de cámara (sobreescribe config.yaml)")
    parser.add_argument("--backend", choices=["dshow", "msmf", "any"],
                        help="Backend de captura de OpenCV")
    parser.add_argument("--delegate", choices=["cpu", "gpu"],
                        help="Delegate de inferencia de MediaPipe")
    parser.add_argument("--dry-run", action="store_true",
                        help="Reconoce y muestra los gestos sin ejecutar acciones reales")
    parser.add_argument("--list-cameras", action="store_true",
                        help="Lista las cámaras detectadas y termina")
    parser.add_argument("-v", "--verbose", action="store_true", help="Registro detallado")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_cameras:
        from .camera import list_cameras

        for backend in ("dshow", "msmf"):
            found = list_cameras(backend)
            print(f"\nBackend {backend}:")
            for index, w, h in found:
                print(f"  índice {index}  →  {w}x{h}")
            if not found:
                print("  (ninguna)")
        return 0

    try:
        cfg = config_module.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2

    if args.camera is not None:
        cfg.camera["index"] = args.camera
    if args.backend:
        cfg.camera["backend"] = args.backend
    if args.delegate:
        cfg.recognizer["delegate"] = args.delegate

    from .app import App

    try:
        return App(cfg, dry_run=args.dry_run).run()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
