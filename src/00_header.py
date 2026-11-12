#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# satchel - GTK4 + libadwaita front end for appimagetool: pick a program, name it, pick an icon, build an .AppImage.
# It packs exactly what you give it. It does NOT copy in shared libraries unless you ask (--bundle-libs, needs linuxdeploy),
# so the result runs where those libraries exist.
# Needs the appimagetool program: put it in your PATH (see README).
# Usage: satchel                      (opens the window)
#        satchel PROGRAM [--name N] [--exe E] [--icon I] [--category C] [--version V] [--comment T] [--terminal]
#                             [--bundle-libs] [--out DIR] [--sandbox] [--try [--seconds S]] [--dry-run] [-v]
#        satchel --install | --uninstall   (copy to ~/.local/bin and add to / remove from the app menu)
import collections, glob, hashlib, json, os, platform, re, shlex, shutil, signal, stat, struct, subprocess, sys, tarfile, tempfile, threading, time, unicodedata, zipfile, zlib
