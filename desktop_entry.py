"""Entry point for the desktop application and the frozen Windows build."""
if __name__ == "__main__":
    from beam_lab.display import enable_dpi_awareness
    enable_dpi_awareness()
    from beam_lab.gui import main
    main()
