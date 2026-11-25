def main(argv):
    if not argv: return run_gui()
    if argv[0] == "--open": return run_gui(argv[1] if len(argv) > 1 and argv[1] else None)    # used by the menu entry's Open With
    try: return cli(argv)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
