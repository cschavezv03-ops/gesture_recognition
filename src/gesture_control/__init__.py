"""Control de herramientas de Windows mediante gestos de la mano.

Pipeline: Iriun Webcam → OpenCV → MediaPipe Tasks (GestureRecognizer) → motor de
gestos → SendInput / Core Audio.
"""

__version__ = "1.0.0"
