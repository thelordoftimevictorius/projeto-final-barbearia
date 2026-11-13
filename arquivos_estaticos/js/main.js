"use strict";

document.addEventListener("DOMContentLoaded", function () {
    const viewport = document.getElementById("feedViewport");
    if (viewport) {
        let isPaused = false;

        window.setInterval(() => {
            if (isPaused) return;

            if (viewport.scrollLeft + viewport.clientWidth >= viewport.scrollWidth - 5) {
                viewport.scrollTo({ left: 0, behavior: "smooth" });
            } else {
                viewport.scrollBy({ left: 300, behavior: "smooth" });
            }
        }, 3000);

        viewport.addEventListener("mouseenter", () => isPaused = true);
        viewport.addEventListener("mouseleave", () => isPaused = false);
        viewport.addEventListener("touchstart", () => isPaused = true, { passive: true });
        viewport.addEventListener("touchend", () => isPaused = false, { passive: true });
    }

    initBarbershopMap();
    initGpsRouteLinks();
    initAppointmentAvailability();
});

function initBarbershopMap() {
    const mapElement = document.getElementById("barbershopMap");
    if (!mapElement || !window.L) return;

    const defaultCenter = [-7.9868, -38.2920];
    const map = window.L.map(mapElement).setView(defaultCenter, 14);
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);

    const markers = [];
    const markersByShopId = new Map();
    document.querySelectorAll("#barbershopCards [data-shop-lat][data-shop-lng]").forEach((card) => {
        const latitude = Number.parseFloat(card.dataset.shopLat);
        const longitude = Number.parseFloat(card.dataset.shopLng);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return;

        const marker = window.L.circleMarker([latitude, longitude], {
            radius: 9,
            color: "#fff",
            weight: 2,
            fillColor: "#a77d3f",
            fillOpacity: 1,
        }).addTo(map);

        const popup = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = card.dataset.shopName || "Barbearia";
        const address = document.createElement("div");
        address.textContent = card.dataset.shopAddress || "";
        const route = document.createElement("a");
        route.href = createDirectionsUrl(latitude, longitude);
        route.dataset.routeGps = "";
        route.dataset.destinationLat = String(latitude);
        route.dataset.destinationLng = String(longitude);
        route.dataset.destinationAddress = card.dataset.shopAddress || "";
        route.target = "_blank";
        route.rel = "noopener noreferrer";
        route.textContent = "Calcular rota";
        popup.append(name, address, route);
        marker.bindPopup(popup);
        markers.push(marker);
        markersByShopId.set(card.dataset.shopId, marker);
    });

    const selectedMarker = markersByShopId.get(mapElement.dataset.selectedShopId);
    if (selectedMarker) {
        map.setView(selectedMarker.getLatLng(), 17);
        selectedMarker.openPopup();
    } else if (markers.length > 1) {
        map.fitBounds(window.L.featureGroup(markers).getBounds().pad(0.15), { maxZoom: 16 });
    } else if (markers.length === 1) {
        map.setView(markers[0].getLatLng(), 15);
    }

    window.setTimeout(() => map.invalidateSize(), 0);
}

function createDirectionsUrl(latitude, longitude, origin) {
    const parameters = new URLSearchParams({
        api: "1",
        destination: `${latitude},${longitude}`,
        travelmode: "driving",
        dir_action: "navigate",
    });

    if (origin) {
        parameters.set("origin", `${origin.latitude},${origin.longitude}`);
    }

    return `https://www.google.com/maps/dir/?${parameters.toString()}`;
}

function initGpsRouteLinks() {
    document.addEventListener("click", (event) => {
        const link = event.target.closest("[data-route-gps]");
        if (!link) return;

        event.preventDefault();
        const latitude = Number.parseFloat(link.dataset.destinationLat);
        const longitude = Number.parseFloat(link.dataset.destinationLng);
        if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) {
            window.open(link.href, "_blank", "noopener,noreferrer");
            return;
        }

        const status = link.parentElement.querySelector("[data-route-status]");
        const routeWindow = window.open("about:blank", "_blank");
        if (routeWindow) routeWindow.opener = null;

        const navigate = (url) => {
            if (routeWindow) {
                routeWindow.location.replace(url);
            } else {
                window.location.assign(url);
            }
        };

        const fallbackUrl = createDirectionsUrl(latitude, longitude);
        if (!navigator.geolocation) {
            if (status) status.textContent = "GPS indisponível; abrindo rota pelo endereço do destino.";
            navigate(fallbackUrl);
            return;
        }

        if (status) status.textContent = "Aguardando permissão para acessar sua localização...";
        navigator.geolocation.getCurrentPosition(
            (position) => {
                const currentPosition = {
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude,
                };
                if (status) status.textContent = "Abrindo rota a partir da sua localização.";
                navigate(createDirectionsUrl(latitude, longitude, currentPosition));
            },
            () => {
                if (status) status.textContent = "Não foi possível acessar o GPS; abrindo a rota pelo destino.";
                navigate(fallbackUrl);
            },
            { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
        );
    });
}

function initAppointmentAvailability() {
    const form = document.getElementById("clientAppointmentForm");
    if (!form) return;

    const shopField = form.querySelector('[name="shop_id"]');
    const barberField = form.querySelector('[name="barber_id"]');
    const serviceField = form.querySelector('[name="service_id"]');
    const dateField = form.querySelector('[name="appointment_date"]');
    const timeField = form.querySelector('[name="appointment_time"]');
    const status = document.getElementById("availabilityStatus");
    let requestId = 0;
    const preferredTime = form.dataset.selectedTime;

    const loadAvailability = async () => {
        const currentRequestId = ++requestId;
        const shopId = shopField ? shopField.value : "";
        const barberId = barberField.value;
        const serviceId = serviceField.value;
        const selectedDate = dateField.value;

        timeField.replaceChildren(new Option("Carregando horários...", ""));
        timeField.disabled = true;
        if (!barberId || !serviceId || !selectedDate) {
            timeField.replaceChildren(new Option("Escolha a data, o serviço e o barbeiro", ""));
            if (status) {
                status.textContent = "Selecione uma data, um serviço e um barbeiro para consultar a disponibilidade.";
            }
            return;
        }

        if (status) status.textContent = "Consultando horários disponíveis...";
        const parameters = new URLSearchParams({
            barber_id: barberId,
            service_id: serviceId,
            date: selectedDate,
        });
        if (shopId) parameters.set("shop_id", shopId);

        try {
            const response = await fetch(`${form.dataset.availabilityUrl}?${parameters.toString()}`, {
                headers: { Accept: "application/json" },
            });
            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || "A consulta de horários falhou.");
            }
            if (currentRequestId !== requestId) return;

            const availableCount = result.slots.filter((slot) => slot.available).length;
            timeField.replaceChildren(new Option("Selecione um horário", ""));
            result.slots.forEach((slot) => {
                const label = slot.available
                    ? `${slot.time} — Disponível`
                    : `${slot.time} — ${slot.reason}`;
                const option = new Option(label, slot.time);
                option.disabled = !slot.available;
                timeField.add(option);
            });
            timeField.disabled = false;

            if (preferredTime) {
                const availableOption = Array.from(timeField.options).find(
                    (option) => option.value === preferredTime && !option.disabled,
                );
                if (availableOption) timeField.value = preferredTime;
            }

            if (status) {
                status.textContent = availableCount
                    ? `${availableCount} horários disponíveis para este barbeiro nesta data.`
                    : "Não há horários disponíveis para este barbeiro nesta data.";
            }
        } catch (error) {
            if (currentRequestId !== requestId) return;
            timeField.replaceChildren(new Option("Disponibilidade indisponível", ""));
            if (status) status.textContent = "Não foi possível consultar os horários. Tente novamente.";
            console.error("Falha ao consultar horários da barbearia:", error);
        }
    };

    [barberField, serviceField, dateField].forEach((field) => {
        field.addEventListener("change", loadAvailability);
    });
    loadAvailability();
}

function scrollCatalog(direction) {
    const viewport = document.getElementById("feedViewport");
    if (viewport) {
        viewport.scrollBy({ left: direction * 300, behavior: "smooth" });
    }
}
