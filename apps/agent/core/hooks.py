"""Post-message hooks for implicit data extraction."""

import re


def extract_implicit_data(text: str) -> dict:
    """Extract user data from natural conversation text without LLM.

    Uses regex patterns for fast, deterministic extraction.
    """
    data: dict = {}
    text_lower = text.lower()

    # Name: "soy Maria", "me llamo Juan", "mi nombre es Pedro"
    name_patterns = [
        r"(?:soy|me llamo|mi nombre es)\s+([A-Z][a-záéíóú]+)",
        r"(?:soy|me llamo|mi nombre es)\s+([a-záéíóú]+)",
    ]
    for pattern in name_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip().title()
            if len(name) >= 2 and name.lower() not in ("el", "la", "un", "una", "de"):
                data["name"] = name
                break

    # District: known Lima districts
    districts = [
        "Miraflores", "Surquillo", "San Miguel", "Pueblo Libre",
        "Santa Catalina", "Monterrico", "Surco", "San Isidro",
        "Barranco", "Lince", "Jesus Maria", "Magdalena",
        "San Borja", "La Molina", "Breña", "Cercado de Lima",
    ]
    for district in districts:
        if district.lower() in text_lower:
            data["district"] = district
            break

    # Bedrooms: "2 dormitorios", "3 dorms", "2 cuartos", "de 2"
    bedroom_match = re.search(r"(\d)\s*(?:dormitorio|dorm|cuarto|habitacion)", text_lower)
    if bedroom_match:
        data["bedrooms"] = int(bedroom_match.group(1))

    # Budget: "300k", "300 mil", "S/300,000", "presupuesto 300000"
    budget_patterns = [
        (r"(\d{3})\s*(?:k|mil)", lambda m: int(m.group(1)) * 1000),
        (r"s/?\.?\s*(\d{3}[,.]?\d{3})", lambda m: int(m.group(1).replace(",", "").replace(".", ""))),
        (r"(\d{6})", lambda m: int(m.group(1))),
    ]
    for pattern, parser in budget_patterns:
        match = re.search(pattern, text_lower)
        if match:
            data["budget"] = parser(match)
            break

    # Phone: 9 digits starting with 9
    phone_match = re.search(r"\b(9\d{8})\b", text)
    if phone_match:
        data["phone"] = phone_match.group(1)

    # Email
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    if email_match:
        data["email"] = email_match.group(0)

    # Purpose: "invertir", "vivir", "inversion"
    if any(w in text_lower for w in ("invertir", "inversion", "rentabilidad", "renta")):
        data["purpose"] = "investment"
    elif any(w in text_lower for w in ("vivir", "mudarme", "familia", "hogar")):
        data["purpose"] = "primary_home"

    return data
