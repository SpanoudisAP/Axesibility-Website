
(function () {
    const svg = document.getElementById("progress-chart");
    if (!svg) {return;}

    const data = JSON.parse(document.getElementById("comparison-chart-data").textContent);
    if (data.length < 2) { return;}

    const namespace = "http://www.w3.org/2000/svg";
    const width = 800;
    const height = 340;
    const margin = {top: 25, right: 30, bottom: 55, left: 55};
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    const series = [
        { key: "critical", color: "#b91c1c" },
        { key: "serious", color: "#c2410c" },
        { key: "moderate", color: "#a16207" },
        { key: "minor", color: "#2563eb" }
    ];
    const values = data.flatMap(item => series.map(s => Number(item[s.key]) || 0));
    const maximum = Math.max(1, ...values);
    const axisMaximum = Math.max( 4, Math.ceil(maximum / 4) * 4);

    function addElement(tag, attributes, text) {
        const element = document.createElementNS(namespace, tag );
        Object.entries(attributes).forEach(([name, value]) => {element.setAttribute(name, value);});

        if (text !== undefined) {element.textContent = text;}
        svg.appendChild(element);
        return element;
    }

    function xPosition(index) {return margin.left + (index / (data.length - 1)) * plotWidth;}
    function yPosition(value) {return margin.top + plotHeight - (value / axisMaximum) * plotHeight;}

    // horizontal grid lines and value labels
    for (let step = 0; step <= 4; step++) {
        const value = axisMaximum * step / 4;
        const y = yPosition(value);

        addElement("line", {
            x1: margin.left,
            y1: y,
            x2: width - margin.right,
            y2: y,
            class: "grid-line"
        });

        addElement("text", {
            x: margin.left - 10,
            y: y + 4,
            "text-anchor": "end"
        }, String(value));
    }

    // label the horizontal axis by scan order
    data.forEach((item, index) => {
        addElement("text", {
            x: xPosition(index),
            y: height - 25,
            "text-anchor": "middle"
        }, String(index + 1));
    });

    addElement("text", {
        x: width / 2,
        y: height - 5,
        "text-anchor": "middle"
    }, "Scan number (oldest to newest)");

    // Draw each severity as a separate line
    series.forEach(s => {
        const points = data.map(
            (item, index) => {
                const x = xPosition(index);
                const y = yPosition(Number(item[s.key]) || 0);
                return `${x},${y}`;
            }
        ).join(" ");

        addElement("polyline", {
            points: points,
            fill: "none",
            stroke: s.color,
            "stroke-width": 3,
            "stroke-linecap": "round",
            "stroke-linejoin": "round"
        });

        data.forEach((item, index) => {
            addElement("circle", {
                cx: xPosition(index),
                cy: yPosition(Number(item[s.key]) || 0), r: 4, fill: s.color});
        });
    });
})();