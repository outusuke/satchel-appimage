def main(argv):
    if not argv: return run_gui()
    try: return cli(argv)
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
