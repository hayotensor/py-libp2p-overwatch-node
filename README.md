# py-libp2p-overwatch-node

A libp2p Overwatch node implementation.

## Installation

### From Source

Clone the repository and install:

```bash
git clone https://github.com/hayotensor/py-libp2p-overwatch.git
cd py-libp2p-overwatch
python -m venv .venv
source .venv/bin/activate
pip install .
touch .env
```

### Development Installation

For development, install with dev dependencies:

```bash
pip install -e ".[dev]"
```

```python
# Import your package
import subnet

# Add usage examples here
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=subnet --cov-report=html

# Run specific test file
pytest tests/test_example.py
```

### Running Locally

The Overwatch node can be run locally with no blockchain integration for testing purposes.

The following "local" documentation relies on the subnet-template running locally. The local database for the [subnet-template](https://github.com/hayotensor/py-libp2p-subnet) will automatically seed the network with the expected overwatch node (`overwatch.key`). The local database here will automatically seed the subnet with all the test nodes and bootnodes (`alith` - `ian`) that the subnet-template should be running.

#### Start subnet

See [subnet-template](https://github.com/hayotensor/py-libp2p-subnet) for more information. 

#### Start Overwatch Node

```bash
python -m subnet.cli.run_node \
--private_key_path overwatch.key \
--overwatch_node_id 1 \
--no_blockchain_rpc
```

### Running Local RPC (Local BLockchain)

Start the blockchain (See [GitHub](https://github.com/hypertensor-blockchain/hypertensor-blockchain))

See [subnet-template](https://github.com/hayotensor/py-libp2p-subnet) for registering the subnet, registering the nodes, and activating the subnet.

#### Register Overwatch Node


### Code Quality

This project uses several tools to maintain code quality:

- **Black**: Code formatting
- **isort**: Import sorting
- **flake8**: Linting
- **mypy**: Type checking
- **pytest**: Testing

Run all quality checks:

```bash
make lint
make test
```

### Pre-commit Hooks

Install pre-commit hooks:

```bash
pre-commit install
```

## Documentation

Coming soon...

## Future

- Implement per subnet-epoch average scores
  - In the current implemention, scores are generated at the end of the overwatch epoch based on the current subnet nodes list.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.
